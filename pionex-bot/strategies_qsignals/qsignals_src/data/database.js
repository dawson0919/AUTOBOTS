// Supabase Database Connection
const { createClient } = require('@supabase/supabase-js');

const SUPABASE_URL = process.env.SUPABASE_URL || 'https://zrhussirvsgsoffmrkxb.supabase.co';
const SUPABASE_ANON_KEY = process.env.SUPABASE_ANON_KEY || 'sb_publishable_MERSBCkwCzs880gVcz_J7Q_urxy9azM';
const SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || '';

let supabase = null;
let supabaseAdmin = null;

function initSupabase() {
    if (!supabase) {
        supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
    }
    return supabase;
}

function getSupabase() {
    if (!supabase) return initSupabase();
    return supabase;
}

async function testConnection() {
    try {
        const { data, error } = await getSupabase().from('candles').select('count', { count: 'exact', head: true });
        if (error) throw error;
        return { success: true, message: 'Supabase connected successfully', count: data };
    } catch (err) {
        console.error('[DB] Connection test failed:', err.message);
        return { success: false, message: err.message };
    }
}

// Service Role Client - Bypasses ALL RLS policies. Use ONLY for admin operations.
function getAdminClient() {
    if (!supabaseAdmin) {
        if (!SUPABASE_SERVICE_ROLE_KEY) {
            console.error('[DB] WARNING: SUPABASE_SERVICE_ROLE_KEY not set! Admin queries will fail.');
            return getSupabase();
        }
        supabaseAdmin = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
            auth: { autoRefreshToken: false, persistSession: false },
            global: {
                headers: { 'x-client-info': 'quant-signal-admin' }
            }
        });
        console.log('[DB] Admin client initialized (Bypasses RLS)');
    }
    return supabaseAdmin;
}

function getAuthenticatedClient(token) {
    if (!token) return getSupabase();
    return createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
        global: {
            headers: { Authorization: `Bearer ${token}` }
        }
    });
}

// Insert candles in bulk (upsert)
async function insertCandles(symbol, timeframe, candles) {
    const rows = candles.map(c => ({
        symbol,
        timeframe,
        open_time: c.openTime,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        volume: c.volume,
        close_time: c.closeTime
    }));

    // Batch insert in chunks of 500
    const chunkSize = 500;
    for (let i = 0; i < rows.length; i += chunkSize) {
        const chunk = rows.slice(i, i + chunkSize);
        const { error } = await getSupabase()
            .from('candles')
            .upsert(chunk, { onConflict: 'symbol,timeframe,open_time' });

        if (error) {
            console.error(`[DB] Insert error for ${symbol}:`, error.message);
            throw error;
        }
    }
    return rows.length;
}

// Get candles from DB
async function getCandles(symbol, timeframe, startTime, endTime) {
    let query = getSupabase()
        .from('candles')
        .select('*')
        .eq('symbol', symbol)
        .eq('timeframe', timeframe)
        .order('open_time', { ascending: false });

    if (startTime) query = query.gte('open_time', startTime);
    if (endTime) query = query.lte('open_time', endTime);

    const { data, error } = await query.limit(2000);
    if (error) {
        console.error('[DB] Query error:', error.message);
        return [];
    }
    return (data || []).reverse();
}

// Get latest candle timestamp for a symbol
async function getLatestCandleTime(symbol, timeframe) {
    const { data, error } = await getSupabase()
        .from('candles')
        .select('open_time')
        .eq('symbol', symbol)
        .eq('timeframe', timeframe)
        .order('open_time', { ascending: false })
        .limit(1);

    if (error || !data || data.length === 0) return null;
    return data[0].open_time;
}

// Get latest close price for a symbol
async function getLatestClosePrice(symbol) {
    // Try 1h timeframe first (more granular), then 4h
    const { data, error } = await getSupabase()
        .from('candles')
        .select('close, open_time')
        .eq('symbol', symbol)
        .order('open_time', { ascending: false })
        .limit(1);

    if (error || !data || data.length === 0) return null;
    return data[0].close;
}

// Get candle count
async function getCandleCount(symbol, timeframe) {
    const { count, error } = await getSupabase()
        .from('candles')
        .select('*', { count: 'exact', head: true })
        .eq('symbol', symbol)
        .eq('timeframe', timeframe);

    if (error) return 0;
    return count || 0;
}

// --- User Profiles ---
async function getProfile(userId, token) {
    const { data, error } = await getAuthenticatedClient(token)
        .from('profiles')
        .select('*')
        .eq('id', userId)
        .single();
    if (error) return null;

    return data;
}

async function updateProfile(userId, updates, token) {
    const { data, error } = await getAuthenticatedClient(token)
        .from('profiles')
        .update(updates)
        .eq('id', userId);
    if (error) throw error;
    return data;
}

// --- Subscriptions ---
async function getSubscriptions(userId, token) {
    const { data, error } = await getAuthenticatedClient(token)
        .from('subscriptions')
        .select('*')
        .eq('user_id', userId);
    if (error) return [];
    return data;
}

async function addSubscription(userId, strategyId, token, symbol = 'BTCUSDT', timeframe = '4h') {
    // USE ADMIN CLIENT to bypass potential RLS issues with new columns
    const client = getAdminClient();

    console.log(`[DB] Adding Sub: User=${userId}, Strat=${strategyId}, Sym=${symbol}, TF=${timeframe}`);

    // 1. Try Optimized Upsert (New Schema: ID + Symbol + Timeframe)
    // We explicitly specify onConflict to target the NEW constraint.
    const { data, error } = await client
        .from('subscriptions')
        .upsert(
            { user_id: userId, strategy_id: strategyId, symbol, timeframe },
            { onConflict: 'user_id, strategy_id, symbol, timeframe' } // Removed ignore duplicates to force error on conflict
        )
        .select();

    if (error) {
        console.warn(`[DB] Enhanced subscription upsert failed: ${error.message}. Attempting cleanup...`);

        // 2. Conflict Handling (Likely "Key (user_id, strategy_id)=(...) already exists")
        // If we can't coexist (due to old DB constraint), we SWAP.
        // DELETE existing subscription for this strategy to make room.
        const { error: delError } = await client
            .from('subscriptions')
            .delete()
            .eq('user_id', userId)
            .eq('strategy_id', strategyId); // Delete ANY version of this strategy

        if (!delError) {
            console.log(`[DB] Cleared old subscription for ${strategyId} to allow new insertion.`);
            // 3. Retry Insert after Delete
            const { data: retryData, error: retryError } = await client
                .from('subscriptions')
                .insert(
                    { user_id: userId, strategy_id: strategyId, symbol, timeframe }
                )
                .select();

            if (retryError) {
                console.error(`[DB] Retry insert failed: ${retryError.message}`);
                throw retryError;
            }
            return retryData;
        } else {
            console.error(`[DB] Failed to delete old sub: ${delError.message}`);
        }

        // Fallback to legacy (just in case it's a completely different error)
        return { success: false, error: error.message };
    }

    return data;
}

async function removeSubscription(userId, strategyId, token, symbol = 'BTCUSDT', timeframe = '4h') {
    const { error } = await getAuthenticatedClient(token)
        .from('subscriptions')
        .delete()
        .eq('user_id', userId)
        .eq('strategy_id', strategyId)
        .eq('symbol', symbol)
        .eq('timeframe', timeframe);
    if (error) throw error;
}

// Update latest_signal for a subscription (Persistent Cache)
async function updateSubscriptionSignal(userId, strategyId, symbol, timeframe, signalData) {
    const supabase = getAdminClient();
    if (!userId) return;

    try {
        console.log(`[DB] Aggressive sync for ${userId} - ${strategyId}`);

        // Precise match with symbol normalization
        const cleanTf = (timeframe || '4h').toLowerCase();
        let cleanSym = (symbol || 'BTCUSDT').toUpperCase();
        if (!cleanSym.endsWith('USDT')) cleanSym += 'USDT';

        const { error, data } = await supabase
            .from('subscriptions')
            .update({ latest_signal: signalData })
            .match({
                user_id: userId,
                strategy_id: strategyId,
                symbol: cleanSym,
                timeframe: cleanTf
            });

        if (error) throw error;
    } catch (e) {
        console.error('[DB] Signal Persistence Exception:', e.message);
    }
}

// --- Premium Applications ---
async function applyPremium(userId, email, account, proofUrls = []) {
    // Use admin client to bypass potential RLS policy issues (e.g. recursion or schema sync issues).
    // Security is already handled by the requireAuth middleware in the route.
    const { data, error } = await getAdminClient()
        .from('premium_applications')
        .insert({
            user_id: userId,
            email,
            trading_account: account,
            proof_urls: proofUrls
        });
    if (error) throw error;
    return data;
}

async function getApplications(token) {
    // Use admin client to bypass RLS for admin operations
    const { data, error } = await getAdminClient()
        .from('premium_applications')
        .select('*')
        .order('created_at', { ascending: false });

    if (error) {
        console.error('[DB] getApplications Error:', error.message, error.details);
        return [];
    }
    return data;
}

async function updateApplicationStatus(id, userId, status, token) {
    // Use admin client to bypass RLS for admin operations
    const { error: appError } = await getAdminClient()
        .from('premium_applications')
        .update({ status })
        .eq('id', id);
    if (appError) throw appError;

    // 2. If approved, upgrade profile role
    if (status === 'approved') {
        const { error: profileError } = await getAdminClient()
            .from('profiles')
            .update({ role: 'advanced' })
            .eq('id', userId);
        if (profileError) throw profileError;
    }
}

// --- Admin: User Management ---
async function getAllUsers(token) {
    // Use admin client (Service Role) to bypass RLS entirely
    const { data, error } = await getAdminClient()
        .from('profiles')
        .select('*');

    if (error) {
        console.error('[DB] getAllUsers Error:', error.message, error.details);
        return [];
    }

    console.log(`[DB] getAllUsers result count: ${data ? data.length : 0}`);

    // If profiles is empty, try to sync from auth.users
    if (!data || data.length === 0) {
        console.log('[DB] Profiles table is empty. Attempting to sync from auth.users...');
        const adminClient = getAdminClient();
        const { data: authUsers, error: authErr } = await adminClient.auth.admin.listUsers();
        if (authErr) {
            console.error('[DB] Failed to list auth users:', authErr.message);
            return [];
        }
        // Insert all auth users into profiles
        for (const u of (authUsers?.users || [])) {
            await adminClient.from('profiles').upsert({
                id: u.id,
                email: u.email,
                role: (process.env.ADMIN_EMAILS || '').split(',').map(e => e.trim()).includes(u.email) ? 'admin' : 'standard'
            }, { onConflict: 'id' });
        }
        console.log(`[DB] Synced ${authUsers?.users?.length || 0} users from auth.users`);
        // Re-query
        const { data: refreshed } = await adminClient.from('profiles').select('*');
        return refreshed || [];
    }

    return data;
}

async function adminUpdateUserRole(userId, role) {
    const { error } = await getAdminClient()
        .from('profiles')
        .update({ role })
        .eq('id', userId);
    if (error) throw error;
}

async function deleteUser(userId, token) {
    // Use admin client to delete profile (bypasses RLS)
    const { error } = await getAdminClient()
        .from('profiles')
        .delete()
        .eq('id', userId);
    if (error) throw error;
}

// Manual Signals
async function getManualSignals(symbol = null, limit = 50) {
    let query = getAdminClient()
        .from('manual_signals')
        .select('*')
        .order('entry_time', { ascending: false });

    if (symbol) query = query.eq('symbol', symbol);
    if (limit) query = query.limit(limit);

    const { data, error } = await query;
    if (error) throw error;
    return data;
}

async function addManualSignal(signal) {
    const { data, error } = await getAdminClient()
        .from('manual_signals')
        .insert([{
            symbol: signal.symbol,
            type: signal.type,
            entry_price: parseFloat(signal.entry_price),
            status: 'active',
            entry_time: new Date(),
            comment: signal.comment
        }])
        .select();
    if (error) throw error;
    return data[0];
}

async function closeManualSignal(id, exitPrice) {
    // Fetch entry price first to calculate ROI
    const { data: signal, error: fetchError } = await getAdminClient()
        .from('manual_signals')
        .select('*')
        .eq('id', id)
        .single();

    if (fetchError) throw fetchError;

    let roi = 0;
    if (signal.type === 'BUY') {
        roi = ((exitPrice - signal.entry_price) / signal.entry_price) * 100;
    } else {
        roi = ((signal.entry_price - exitPrice) / signal.entry_price) * 100;
    }

    const { data, error } = await getAdminClient()
        .from('manual_signals')
        .update({
            exit_price: parseFloat(exitPrice),
            status: 'closed',
            closed_at: new Date(),
            roi: parseFloat(roi.toFixed(2))
        })
        .eq('id', id)
        .select();

    if (error) throw error;
    return data[0];
}

async function deleteManualSignal(id) {
    const { error } = await getAdminClient()
        .from('manual_signals')
        .delete()
        .eq('id', id);
    if (error) throw error;
}

// --- Strategy Performance Cache ---
// Reads all cached backtest summaries from DB (public read, no auth needed)
async function getAllStrategyPerformance() {
    const { data, error } = await getSupabase()
        .from('strategy_performance')
        .select('strategy_id, symbol, timeframe, total_return, win_rate, profit_factor, total_trades, latest_signal, computed_at');

    if (error) {
        console.error('[DB] getAllStrategyPerformance error:', error.message);
        return [];
    }
    return data || [];
}

// Upsert a single backtest result (called by backtest engine after computing)
async function upsertStrategyPerformance(strategyId, symbol, timeframe, summary, latestSignal) {
    const { error } = await getAdminClient()
        .from('strategy_performance')
        .upsert({
            strategy_id: strategyId,
            symbol,
            timeframe,
            total_return: summary?.totalReturn ?? null,
            win_rate: summary?.winRate ?? null,
            profit_factor: summary?.profitFactor ?? null,
            total_trades: summary?.totalTrades ?? null,
            latest_signal: latestSignal || null,
            computed_at: new Date().toISOString()
        }, { onConflict: 'strategy_id,symbol,timeframe' });

    if (error) {
        console.error('[DB] upsertStrategyPerformance error:', error.message);
    }
}

// ── Visitor Counter (Supabase-backed, IP-deduped per day) ──

async function getVisitorCount() {
    const { data, error } = await getAdminClient()
        .from('site_stats')
        .select('value')
        .eq('key', 'visitor_count')
        .single();
    if (error) throw error;
    return data?.value || 0;
}

async function incrementVisitorCount() {
    const admin = getAdminClient();
    // Read current value
    const { data: row } = await admin
        .from('site_stats')
        .select('value')
        .eq('key', 'visitor_count')
        .single();

    const newCount = (row?.value || 0) + 1;
    await admin
        .from('site_stats')
        .upsert({ key: 'visitor_count', value: newCount, updated_at: new Date().toISOString() }, { onConflict: 'key' });
    return newCount;
}

module.exports = {
    initSupabase,
    getSupabase,
    getAdminClient,
    getAuthenticatedClient,
    testConnection,
    insertCandles,
    getCandles,
    getLatestCandleTime,
    getCandleCount,
    getLatestClosePrice,
    // Membership & Subscriptions
    getProfile,
    updateProfile,
    getSubscriptions,
    addSubscription,
    removeSubscription,
    updateSubscriptionSignal,
    applyPremium,
    getApplications,
    updateApplicationStatus,
    getAllUsers,
    adminUpdateUserRole,
    deleteUser,
    // Manual Signals
    getManualSignals,
    addManualSignal,
    closeManualSignal,
    deleteManualSignal,
    // Strategy Performance Cache
    getAllStrategyPerformance,
    upsertStrategyPerformance,
    // Visitor Counter
    getVisitorCount,
    incrementVisitorCount
};
