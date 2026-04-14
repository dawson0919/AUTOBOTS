// API Routes
const express = require('express');
const crypto = require('crypto');
const router = express.Router();
const rateLimit = require('express-rate-limit');

// --- Rate Limiters ---
// General limiter: 600 requests per 15 minutes per real IP
// (enough for normal page loads which fire ~8-10 API calls at once)
const generalLimiter = rateLimit({
    windowMs: 15 * 60 * 1000,
    max: 600,
    standardHeaders: true,
    legacyHeaders: false,
    message: { error: 'Too many requests, please try again later.' }
});

// Auth/write-sensitive endpoints: stricter limit
const authLimiter = rateLimit({
    windowMs: 15 * 60 * 1000,
    max: 30,
    standardHeaders: true,
    legacyHeaders: false,
    message: { error: 'Too many auth requests, please try again later.' }
});

// Backtest: CPU-heavy, limit per minute
const backtestLimiter = rateLimit({
    windowMs: 60 * 1000,
    max: 10,
    standardHeaders: true,
    legacyHeaders: false,
    message: { error: 'Backtest rate limit exceeded. Please wait a moment.' }
});

router.use(generalLimiter);
const Backtester = require('../engine/backtester');
const { getCandleData } = require('../engine/dataFetcher');
const { parsePineScript } = require('../engine/pineParser');
const { getCurrentPrices } = require('../data/priceMonitor');
const { createLinkCode, processUpdate } = require('../services/telegramBot');
const {
    getCandles,
    getCandleCount,
    getProfile,
    getSubscriptions,
    addSubscription,
    removeSubscription,
    updateSubscriptionSignal,
    applyPremium,
    getApplications,
    updateApplicationStatus,
    getAllUsers,
    deleteUser,
    getSupabase,
    getAdminClient,
    getAuthenticatedClient,
    getManualSignals,
    addManualSignal,
    closeManualSignal,
    deleteManualSignal,
    getAllStrategyPerformance,
    upsertStrategyPerformance,
    getVisitorCount,
    incrementVisitorCount
} = require('../data/database');
const indicators = require('../engine/indicators');

// Strategy registry
const strategies = {};
const backtestCache = {};

// Load built-in strategies
const ma60 = require('../engine/strategies/ma60');
strategies[ma60.id] = ma60;

const threeStyle = require('../engine/strategies/threeStyle');
strategies[threeStyle.id] = threeStyle;

const turtleBreakout = require('../engine/strategies/turtleBreakout');
strategies[turtleBreakout.id] = turtleBreakout;

const dualEma = require('../engine/strategies/dualEma');
strategies[dualEma.id] = dualEma;

const macdMa = require('../engine/strategies/macdMa');
strategies[macdMa.id] = macdMa;

const granvilleEth4h = require('../engine/strategies/granville_eth_4h');
strategies[granvilleEth4h.id] = granvilleEth4h;

const dualStBreakout = require('../engine/strategies/dualSuperTrend');
strategies[dualStBreakout.id] = dualStBreakout;

const donchianTrend = require('../engine/strategies/donchianTrend');
strategies[donchianTrend.id] = donchianTrend;

const ichimokuCloud = require('../engine/strategies/ichimoku_cloud');
strategies[ichimokuCloud.id] = ichimokuCloud;

// Health Check
router.get('/health', (req, res) => {
    res.json({ status: 'ok', uptime: process.uptime() });
});

// Strategy Performance Cache (DB-backed, replaces per-card backtest calls on homepage)
router.get('/strategies/performance', async (req, res) => {
    try {
        const rows = await getAllStrategyPerformance();
        // Transform to a map keyed by "strategyId_symbol_timeframe" for easy lookup
        const map = {};
        for (const r of rows) {
            const key = `${r.strategy_id}_${r.symbol}_${r.timeframe}`;
            map[key] = {
                totalReturn: r.total_return,
                winRate: r.win_rate,
                profitFactor: r.profit_factor,
                totalTrades: r.total_trades,
                latestSignal: r.latest_signal,
                computedAt: r.computed_at
            };
        }
        res.json(map);
    } catch (e) {
        console.error('[StrategyPerformance] Error:', e.message);
        res.status(500).json({ error: e.message });
    }
});

// List all strategies
router.get('/strategies', async (req, res) => {
    // Check if admin to show hidden
    let isAdmin = false;
    const authHeader = req.headers.authorization;
    if (authHeader) {
        const token = authHeader.split(' ')[1];
        const { data } = await getSupabase().auth.getUser(token);
        if (data.user) {
            const profile = await getProfile(data.user.id, token);
            const adminEmails = (process.env.ADMIN_EMAILS || '').split(',');
            if (profile?.role === 'admin' || adminEmails.includes(data.user.email)) {
                isAdmin = true;
            }
        }
    }

    const list = Object.values(strategies)
        .filter(s => isAdmin || s.isVisible !== false)
        .map(s => ({
            id: s.id,
            name: s.name,
            description: s.description,
            category: s.category || 'Basic',
            author: s.author || 'QuantSignal',
            isVisible: s.isVisible !== false // Default true
        }));
    res.json({ strategies: list });
});

// Admin: Toggle Strategy Visibility
router.post('/admin/strategies/toggle-visibility', requireAdmin, (req, res) => {
    const { id, isVisible } = req.body;
    if (strategies[id]) {
        strategies[id].isVisible = isVisible;
        res.json({ success: true, isVisible });
    } else {
        res.status(404).json({ error: 'Strategy not found' });
    }
});

// Get strategy by ID
router.get('/strategies/:id', async (req, res) => {
    const s = strategies[req.params.id];
    if (!s) return res.status(404).json({ error: 'Strategy not found' });

    // Check if admin to show sensitive info
    let isAdmin = false;
    const authHeader = req.headers.authorization;
    if (authHeader) {
        try {
            const token = authHeader.split(' ')[1];
            const { data } = await getSupabase().auth.getUser(token);
            if (data.user) {
                const profile = await getProfile(data.user.id, token);
                const adminEmails = (process.env.ADMIN_EMAILS || '').split(',');
                if (profile?.role === 'admin' || adminEmails.includes(data.user.email)) {
                    isAdmin = true;
                }
            }
        } catch (e) {
            console.warn('[Strategy Detail] Auth check failed:', e.message);
        }
    }

    res.json({
        id: s.id,
        name: s.name,
        description: s.description,
        category: s.category || 'Basic',
        author: s.author || 'Unknown',
        isVisible: s.isVisible !== false,
        adminNotes: isAdmin ? s.adminNotes : null
    });
});

// --- Auth Middleware ---
async function requireAuth(req, res, next) {
    const authHeader = req.headers.authorization;
    if (!authHeader) return res.status(401).json({ error: 'Unauthorized' });

    const token = authHeader.split(' ')[1];
    const { data, error } = await getSupabase().auth.getUser(token);

    if (error || !data.user) return res.status(401).json({ error: 'Invalid token' });
    req.user = data.user;
    req.token = token;
    next();
}

async function requireAdmin(req, res, next) {
    await requireAuth(req, res, async () => {
        const profile = await getProfile(req.user.id, req.token);
        if (profile?.role === 'admin') {
            next();
        } else {
            res.status(403).json({ error: 'Admin access required' });
        }
    });
}
// (Debug endpoints removed — use admin panel for diagnostics)

// --- Membership & Subscriptions ---
// --- Membership & Subscriptions ---
router.get('/profile', requireAuth, async (req, res) => {
    try {
        let profile = await getProfile(req.user.id, req.token);
        // Proactive Profile Sync: Ensure user exists in database
        const userEmail = (req.user.email || '').toLowerCase().trim();
        const adminEmails = (process.env.ADMIN_EMAILS || '').split(',');
        const isAdminEmail = adminEmails.some(e => e.toLowerCase().trim() === userEmail);
        const isSuperAdmin = process.env.SUPER_ADMIN_ID && req.user.id === process.env.SUPER_ADMIN_ID;

        const targetRole = (isAdminEmail || isSuperAdmin) ? 'admin' : (profile?.role || 'standard');

        // Always upsert to ensure the profile exists in the DB for Admin Audit
        // We use req.token to ensure we have the right permissions to update our own profile
        const { data: syncedProfile, error: upsertError } = await getAuthenticatedClient(req.token)
            .from('profiles')
            .upsert({
                id: req.user.id,
                email: req.user.email,
                role: targetRole,
                updated_at: new Date().toISOString()
            })
            .select()
            .single();

        if (upsertError) {
            console.error('[Profile Sync] Error:', upsertError.message);
        }

        profile = syncedProfile || profile || {
            id: req.user.id,
            email: req.user.email,
            role: targetRole,
            created_at: new Date().toISOString()
        };

        // Check for own pending application - only if still standard
        if (profile?.role === 'standard') {
            const { data: apps, error: appError } = await getAdminClient()
                .from('premium_applications')
                .select('status')
                .eq('user_id', req.user.id)
                .eq('status', 'pending')
                .limit(1);
            profile.hasPendingApplication = apps && apps.length > 0;
        } else {
            profile.hasPendingApplication = false;
        }

        res.json(profile);
    } catch (e) {
        console.error('Profile fetch error:', e);
        res.status(500).json({ error: 'Failed to fetch profile' });
    }
});

// Upload Proof Screenshot (Server-side to bypass RLS)
router.post('/upload-proof', requireAuth, async (req, res) => {
    const { fileName, fileData } = req.body;
    if (!fileName || !fileData) return res.status(400).json({ error: 'Missing file data' });

    // Validate file extension (whitelist only)
    const ext = fileName.split('.').pop().toLowerCase();
    const allowedExts = ['png', 'jpg', 'jpeg', 'webp'];
    if (!allowedExts.includes(ext)) {
        return res.status(400).json({ error: 'Only PNG, JPG, JPEG, and WEBP files are allowed' });
    }

    // Validate MIME type from data URL
    const mimeMatch = fileData.match(/^data:(image\/(?:png|jpeg|webp));base64,/);
    if (!mimeMatch) {
        return res.status(400).json({ error: 'Invalid image format. Only PNG, JPG, and WEBP are allowed.' });
    }
    const contentType = mimeMatch[1];

    // Check file size (base64 encoded — actual bytes ~= base64len * 0.75)
    const base64Body = fileData.replace(/^data:image\/\w+;base64,/, '');
    const approxBytes = base64Body.length * 0.75;
    if (approxBytes > 5 * 1024 * 1024) { // 5 MB limit
        return res.status(400).json({ error: 'File too large. Maximum size is 5MB.' });
    }

    try {
        const admin = getAdminClient();
        const buffer = Buffer.from(base64Body, 'base64');

        const { data, error } = await admin.storage
            .from('proofs')
            .upload(fileName, buffer, { contentType, upsert: true });

        if (error) throw error;

        const { data: { publicUrl } } = admin.storage.from('proofs').getPublicUrl(fileName);
        res.json({ publicUrl });
    } catch (e) {
        console.error('[UploadProof] Error:', e.message);
        res.status(500).json({ error: e.message });
    }
});

router.post('/apply-premium', authLimiter, requireAuth, async (req, res) => {
    const { account, proofUrls } = req.body;
    if (!account) return res.status(400).json({ error: 'Account required' });
    try {
        // Check if already has a pending application
        const { data: existing } = await getAdminClient()
            .from('premium_applications')
            .select('id')
            .eq('user_id', req.user.id)
            .eq('status', 'pending')
            .limit(1);

        if (existing && existing.length > 0) {
            return res.status(400).json({ error: 'You already have a pending application.' });
        }

        await applyPremium(req.user.id, req.user.email, account, proofUrls || []);
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

router.post('/subscribe', requireAuth, async (req, res) => {
    const { strategyId, symbol, timeframe } = req.body;
    const s = strategies[strategyId];
    if (!s) return res.status(404).json({ error: 'Strategy not found' });

    try {
        // Enforce RBAC
        let profile = await getProfile(req.user.id, req.token);
        const adminEmails = (process.env.ADMIN_EMAILS || '').split(',');

        // Fallback or Override for Admin
        const isSuperAdminUser = process.env.SUPER_ADMIN_ID && req.user.id === process.env.SUPER_ADMIN_ID;
        if (adminEmails.includes(req.user.email) || isSuperAdminUser) {
            profile = { ...profile, role: 'admin' };
        }

        const role = profile?.role || 'standard';

        console.log(`[Subscribe] User: ${req.user.email} (${req.user.id}), Role: ${role}, Strategy: ${s.name} (${s.category}), Symbol: ${symbol}`);

        // Index Restrictions (SPX/NAS)
        const isIndex = ['SPXUSDT', 'NQUSDT', 'ESUSDT', 'CLUSDT', 'SPX', 'NQ', 'ES', 'CL'].includes(symbol);
        // RELAXED: Allow Advanced users to subscribe for now
        if (isIndex && !['admin', 'platinum', 'advanced'].includes(role)) {
            console.log(`[Subscribe] DENIED: ${symbol} requiring Advanced+.`);
            return res.status(403).json({ error: 'Advanced/Platinum Membership required for Index (SPY/QQQ) signals.' });
        }

        if (s.category === 'Premium' && !['admin', 'advanced', 'platinum'].includes(role)) {
            console.log(`[Subscribe] DENIED: Role '${role}' insufficient for Premium strategy.`);
            return res.status(403).json({ error: 'Premium Membership required for this strategy.' });
        }

        await addSubscription(req.user.id, strategyId, req.token, symbol, timeframe);
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

router.post('/unsubscribe', requireAuth, async (req, res) => {
    const { strategyId, symbol, timeframe } = req.body;
    try {
        await removeSubscription(req.user.id, strategyId, req.token, symbol, timeframe);
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

router.get('/subscriptions', requireAuth, async (req, res) => {
    try {
        const subs = await getSubscriptions(req.user.id, req.token);
        const results = [];

        for (const sub of subs) {
            try {
                const s = strategies[sub.strategy_id];
                if (!s) {
                    console.log(`[Subscriptions] Strategy not found: ${sub.strategy_id}`);
                    continue;
                }

                // RESPECT DB VALUES: Use symbol/timeframe from subscription record
                let symbol = sub.symbol || s.defaultParams?.symbol || 'BTCUSDT';
                let timeframe = sub.timeframe || '4h'; // Use DB timeframe or default to 4h
                // STRATEGY: 
                // 1. Check DB Persistence (The new "Direct from Detail" method)
                // 2. Check Memory Cache (The old method)

                let latestSignal = null;
                let performance = {};

                // 1. DB Persistence
                if (sub.latest_signal) {
                    console.log(`[Subscriptions] User ${req.user.id} - FOUND DB Signal for ${sub.strategy_id}`);
                    latestSignal = sub.latest_signal;
                }

                // 2. Memory Cache Fallback (if DB is empty)
                else {
                    const keysToTry = [
                        `${sub.strategy_id}_${symbol}_${timeframe}`,
                        `${sub.strategy_id}_${symbol}_${timeframe.toLowerCase()}`,
                        `${sub.strategy_id}_${symbol}_${timeframe.toUpperCase()}`
                    ];

                    let cachedResult = null;
                    for (const key of keysToTry) {
                        if (backtestCache[key]) {
                            cachedResult = backtestCache[key];
                            break;
                        }
                    }

                    if (cachedResult && cachedResult.recentTrades && cachedResult.recentTrades.length > 0) {
                        // console.log(`[Subscriptions] Using cached result for ${cachedResult.strategy.name}`);
                        latestSignal = cachedResult.recentTrades[0];
                        performance = cachedResult.performance || {};
                    }
                }

                results.push({
                    ...sub,
                    strategy_name: s.name,
                    latest_signal: latestSignal,
                    performance: performance
                });
            } catch (err) {
                console.error(`[Subscriptions] Error processing ${sub.strategy_id} for user ${req.user.id}: ${err.message}`);
                // Continue to next sub, don't crash whole list
                // Optionally push an error state so UI knows?
                results.push({
                    ...sub,
                    strategy_name: strategies[sub.strategy_id]?.name || sub.strategy_id,
                    latest_signal: null,
                    error: "Data unavailable"
                });
            }
        }
        res.json(results);
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// (Duplicate route removed)


// --- Manual Signals Public Performance & Featured ---
router.get('/manual-signals/performance', async (req, res) => {
    try {
        const signals = await getManualSignals(null, 500);
        const closed = signals.filter(s => s.status === 'closed');
        let totalRoi = 0;
        closed.forEach(s => totalRoi += parseFloat(s.roi || 0));
        res.json({ totalRoi, closedCount: closed.length });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// Public Featured Gold Signals — best PAXG strategies from strategy_performance
router.get('/featured-signals/gold', async (req, res) => {
    try {
        const { getAllStrategyPerformance } = require('../data/database');
        const allPerf = await getAllStrategyPerformance();

        // Filter PAXG strategies with valid data, sorted by best total_return
        const paxgPerf = allPerf
            .filter(p => p.symbol === 'PAXGUSDT' && p.latest_signal && p.total_return != null)
            .sort((a, b) => (parseFloat(b.total_return) || 0) - (parseFloat(a.total_return) || 0));

        // Pick best 1h and best 4h by total_return
        const best1h = paxgPerf.find(p => p.timeframe === '1h');
        const best4h = paxgPerf.find(p => p.timeframe === '4h');

        const featured = [best1h, best4h].filter(Boolean).map(p => {
            const sig = p.latest_signal;
            return {
                id: `perf_${p.strategy_id}_${p.timeframe}`,
                symbol: p.symbol,
                strategy_id: p.strategy_id,
                timeframe: p.timeframe,
                type: sig.type === 'LONG' ? 'BUY' : (sig.type === 'SHORT' ? 'SELL' : sig.type),
                entry_price: sig.entryPrice || sig.price || sig.entry_price,
                roi: sig.pnlPercent || sig.roi || 0,
                total_return: parseFloat(p.total_return) || 0,
                win_rate: parseFloat(p.win_rate) || 0,
                status: 'active',
                entry_time: sig.entryTime || sig.entryDate || sig.time,
                comment: `Strategy: ${p.strategy_id.replace(/_/g, ' ').toUpperCase()}`,
                source: 'algo'
            };
        }).filter(s => s.entry_price && s.entry_time);

        res.json(featured);
    } catch (e) {
        console.error('[FeaturedGold] Error:', e.message);
        res.json([]);
    }
});

// CL WTI Crude Oil featured signals (primary featured asset)
router.get('/featured-signals/oil', async (req, res) => {
    try {
        const { getAllStrategyPerformance } = require('../data/database');
        const allPerf = await getAllStrategyPerformance();

        const clPerf = allPerf
            .filter(p => p.symbol === 'CLUSDT' && p.latest_signal && p.total_return != null)
            .sort((a, b) => (parseFloat(b.total_return) || 0) - (parseFloat(a.total_return) || 0));

        const best1h = clPerf.find(p => p.timeframe === '1h');
        const best4h = clPerf.find(p => p.timeframe === '4h');

        const featured = [best4h, best1h].filter(Boolean).map(p => {
            const sig = p.latest_signal;
            return {
                id: `perf_${p.strategy_id}_${p.timeframe}`,
                symbol: p.symbol,
                strategy_id: p.strategy_id,
                timeframe: p.timeframe,
                type: sig.type === 'LONG' ? 'BUY' : (sig.type === 'SHORT' ? 'SELL' : sig.type),
                entry_price: sig.entryPrice || sig.price || sig.entry_price,
                roi: sig.pnlPercent || sig.roi || 0,
                total_return: parseFloat(p.total_return) || 0,
                win_rate: parseFloat(p.win_rate) || 0,
                status: 'active',
                entry_time: sig.entryTime || sig.entryDate || sig.time,
                comment: `Strategy: ${p.strategy_id.replace(/_/g, ' ').toUpperCase()}`,
                source: 'algo'
            };
        }).filter(s => s.entry_price && s.entry_time);

        res.json(featured);
    } catch (e) {
        console.error('[FeaturedOil] Error:', e.message);
        res.json([]);
    }
});

// --- Admin Panel Routes ---
// --- Manual Signals API (Platinum & Admin Only) ---
router.get('/manual-signals', requireAuth, async (req, res) => {
    try {
        const profile = await getProfile(req.user.id, req.token);
        const hasAccess = ['platinum', 'admin'].includes(profile?.role);
        if (!hasAccess) {
            return res.status(403).json({ error: 'Platinum membership required' });
        }

        const symbol = req.query.symbol;
        const limit = Math.min(Math.max(parseInt(req.query.limit) || 50, 1), 200);
        const signals = await getManualSignals(symbol, limit);
        res.json(signals);
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// Admin Manual Signals Management
router.post('/admin/manual-signals', requireAdmin, async (req, res) => {
    try {
        const { symbol, type, entry_price, comment } = req.body;
        if (!symbol || !type || !entry_price) {
            return res.status(400).json({ error: 'Missing required fields' });
        }
        const signal = await addManualSignal({ symbol, type, entry_price, comment });
        res.json({ success: true, signal });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

router.post('/admin/manual-signals/:id/close', requireAdmin, async (req, res) => {
    try {
        const { id } = req.params;
        const { exit_price } = req.body;
        if (!exit_price) return res.status(400).json({ error: 'Exit price required' });
        const signal = await closeManualSignal(id, exit_price);
        res.json({ success: true, signal });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

router.delete('/admin/manual-signals/:id', requireAdmin, async (req, res) => {
    try {
        const { id } = req.params;
        await deleteManualSignal(id);
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// Admin Dashboard - List all signals for management
router.get('/admin/manual-signals', requireAdmin, async (req, res) => {
    try {
        const signals = await getManualSignals(null, 200);
        res.json(signals);
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

router.get('/admin/users', requireAdmin, async (req, res) => {
    try {
        let users = await getAllUsers(req.token);

        // Inject online status
        const onlineIds = (typeof global.getOnlineUsers === 'function') ? global.getOnlineUsers() : [];
        users = users.map(u => ({
            ...u,
            isOnline: onlineIds.includes(u.id)
        }));

        console.log(`[Admin] Fetched ${users.length} users with online status`);
        res.json(users);
    } catch (e) {
        console.error('Admin users fetch error:', e);
        res.status(500).json({ error: e.message });
    }
});

router.get('/admin/applications', requireAdmin, async (req, res) => {
    try {
        const apps = await getApplications(req.token);
        res.json(apps);
    } catch (e) {
        console.error('Admin apps fetch error:', e);
        res.status(500).json({ error: e.message });
    }
});

router.post('/admin/approve-application', requireAdmin, async (req, res) => {
    const { id, userId, status } = req.body; // status: 'approved' or 'rejected'
    try {
        const { updateApplicationStatus } = require('../data/database');
        await updateApplicationStatus(id, userId, status, req.token);
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// Batch approve all pending applications
router.post('/admin/approve-all', requireAdmin, async (req, res) => {
    try {
        const { getApplications, updateApplicationStatus } = require('../data/database');
        const apps = await getApplications(req.token);
        const pending = apps.filter(a => a.status === 'pending');

        let approved = 0;
        const errors = [];
        for (const app of pending) {
            try {
                await updateApplicationStatus(app.id, app.user_id, 'approved', req.token);
                approved++;
            } catch (e) {
                errors.push({ id: app.id, error: e.message });
            }
        }
        res.json({ success: true, approved, errors });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

router.post('/admin/update-user-role', requireAdmin, async (req, res) => {
    try {
        const { userId, role } = req.body;
        if (!userId || !role) return res.status(400).json({ error: 'Missing userId or role' });
        const { adminUpdateUserRole } = require('../data/database');
        await adminUpdateUserRole(userId, role);
        res.json({ success: true, role });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

router.delete('/admin/users/:id', requireAdmin, async (req, res) => {
    try {
        await deleteUser(req.params.id, req.token);
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// Strategy registry functions moved up...


// Add new strategy via PineScript
router.post('/strategies', (req, res) => {
    try {
        const { name, code, category, description } = req.body;
        if (!code) return res.status(400).json({ error: 'PineScript code is required' });

        const parsed = parsePineScript(code);
        const id = name ? name.toLowerCase().replace(/[^a-z0-9]+/g, '_') : `custom_${Date.now()}`;

        strategies[id] = {
            id,
            name: name || parsed.metadata.name,
            description: description || `Custom strategy: ${parsed.metadata.name}`,
            category: category || 'Basic',
            author: 'User',
            pineScript: code,
            execute: parsed.execute
        };

        res.json({ success: true, id, name: strategies[id].name });
    } catch (err) {
        res.status(400).json({ error: `Failed to parse PineScript: ${err.message}` });
    }
});

// Run backtest
router.post('/backtest', backtestLimiter, async (req, res) => {
    try {
        let { strategyId, symbol = 'BTCUSDT', timeframe = '4h', startTime, endTime } = req.body;

        // Clean symbol (remove /, -, etc.)
        symbol = symbol.toUpperCase().replace(/[^A-Z0-9]/g, '');

        // Get or parse strategy
        let strategyFn;
        let strategyName;

        const isIndex = ['SPXUSDT', 'NQUSDT', 'ESUSDT', 'CLUSDT', 'SPX', 'NQ', 'ES', 'CL'].includes(symbol);

        if (strategyId && strategies[strategyId]) {
            const s = strategies[strategyId];
            let params = { ...s.defaultParams, symbol, timeframe };

            if (isIndex && strategyId === 'turtle_breakout') {
                if (['NQUSDT', 'NQ'].includes(symbol) && timeframe === '4h') {
                    params = { leftBars: 12, rightBars: 4, minHoldBars: 2 };
                } else if (timeframe === '4h') {
                    params = { leftBars: 6, rightBars: 5, minHoldBars: 15 };
                } else if (timeframe === '1h') {
                    params = { leftBars: 20, rightBars: 2, minHoldBars: 40 };
                }
            }

            strategyFn = s.createStrategy ? s.createStrategy(params) : s.execute;
            strategyName = s.name;
            console.log(`[Backtest] ${strategyName} using params:`, params);
        } else if (req.body.code) {
            const parsed = parsePineScript(req.body.code);
            strategyFn = parsed.execute;
            strategyName = parsed.metadata.name;
        } else {
            return res.status(400).json({ error: 'Either strategyId or code is required' });
        }

        // Get candle data
        const daysBack = isIndex ? 90 : (timeframe === '1h' ? 45 : 180);
        const candles = await getCandleData(symbol, timeframe, { startTime, endTime, daysBack });
        if (candles.length < 50) {
            return res.status(400).json({ error: `Insufficient data: only ${candles.length} candles available` });
        }

        // Run backtest
        const backtester = new Backtester({
            initialCapital: 10000,
            positionSize: 0.95,
            commission: 0,
            slippage: 0
        });

        console.log(`[Backtest] Running ${strategyName} for ${symbol}...`);
        console.log(`[Backtest] StrategyFn: ${typeof strategyFn}, Candles: ${candles.length}`);

        const result = backtester.run(strategyFn, candles);
        console.log(`[Backtest] Result Summary exists: ${!!result.summary}`);

        result.strategy = { name: strategyName, symbol, timeframe };
        result.dataInfo = {
            totalCandles: candles.length,
            symbol,
            timeframe
        };

        // Cache result (include timeframe in key!)
        const cacheKey = `${strategyId || 'custom'}_${symbol}_${timeframe}_${(req.body.code ? 'custom' : '')}`;
        backtestCache[cacheKey] = result;

        // PERSIST SIGNAL FOR SUBSCRIBED USER (if logged in)
        // This solves the 'Profile Page No Signal' issue permanently.
        const authHeader = req.headers['authorization'];
        let userId = null;
        if (authHeader) {
            const token = authHeader.split(' ')[1];
            if (token) {
                try {
                    // Use Supabase Auth instead of jsonwebtoken (to avoid missing dependency crash)
                    const { data: { user }, error } = await getSupabase().auth.getUser(token);
                    if (user && !error) {
                        userId = user.id;
                    }
                } catch (e) {
                    console.warn('[Backtest] Token verification failed:', e.message);
                }
            }
        }

        const latestSignal = (result.recentTrades && result.recentTrades.length > 0) ? result.recentTrades[0] : null;

        // Always persist performance summary to strategy_performance table (for homepage cache)
        if (strategyId && result.summary) {
            upsertStrategyPerformance(strategyId, symbol, timeframe, result.summary, latestSignal)
                .catch(e => console.warn('[Backtest] Performance cache write failed:', e.message));
        }

        if (userId) {
            console.log(`[Backtest] Persisting signal for user ${userId} on ${symbol} ${timeframe}`);
            await updateSubscriptionSignal(userId, strategyId, symbol, timeframe, latestSignal);
        }

        res.json(result);
    } catch (err) {
        console.error('[Backtest] Error:', err);
        res.status(500).json({ error: `Backtest failed: ${err.message}` });
    }
});

// Get cached backtest result
router.get('/backtest/:key', (req, res) => {
    const result = backtestCache[req.params.key];
    if (!result) return res.status(404).json({ error: 'No cached result found' });
    res.json(result);
});

// Current prices
router.get('/prices/current', (req, res) => {
    res.json(getCurrentPrices());
});

// Historical candles from DB
router.get('/prices/history', async (req, res) => {
    const { symbol = 'BTCUSDT', timeframe = '4h' } = req.query;
    const limit = Math.min(Math.max(parseInt(req.query.limit) || 200, 1), 2000);
    const candles = await getCandles(symbol, timeframe);
    res.json({
        symbol,
        timeframe,
        count: candles.length,
        candles: candles.slice(-limit)
    });
});

// DB stats
router.get('/stats', async (req, res) => {
    const symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XAUUSDT', 'PAXGUSDT', 'SPXUSDT', 'NQUSDT', 'ESUSDT', 'CLUSDT'];
    const stats = {};
    for (const s of symbols) {
        stats[s] = await getCandleCount(s, '4h');
    }
    res.json({ candleCounts: stats, strategies: Object.keys(strategies).length });
});

// Visitor Counter (Supabase-backed, IP-deduped per day)
const seenVisitorIPs = new Map(); // IP → date string (YYYY-MM-DD)

router.get('/visitor-count', async (req, res) => {
    try {
        const ip = req.ip || req.headers['x-forwarded-for']?.split(',')[0]?.trim() || 'unknown';
        const today = new Date().toISOString().split('T')[0];
        const lastSeen = seenVisitorIPs.get(ip);

        if (lastSeen !== today) {
            // New unique visitor today — increment and record
            seenVisitorIPs.set(ip, today);
            const count = await incrementVisitorCount();
            return res.json({ count });
        }

        // Already counted today — just return current count
        const count = await getVisitorCount();
        res.json({ count });
    } catch (e) {
        console.warn('[VisitorCount] Error:', e.message);
        res.json({ count: 0 });
    }
});

// ── Telegram Bot Routes ────────────────────────

// Webhook endpoint (receives bot updates from Telegram)
router.post('/telegram/webhook', async (req, res) => {
    // Verify request is from Telegram using secret token header
    const secretToken = process.env.TELEGRAM_WEBHOOK_SECRET;
    if (secretToken) {
        const provided = req.headers['x-telegram-bot-api-secret-token'];
        if (!provided || !crypto.timingSafeEqual(Buffer.from(provided), Buffer.from(secretToken))) {
            return res.status(403).json({ error: 'Forbidden' });
        }
    }
    try {
        await processUpdate(req.body, { getSupabaseAdmin: getAdminClient });
        res.json({ ok: true });
    } catch (err) {
        console.error('[Telegram Webhook] Error:', err);
        res.json({ ok: true }); // Always return 200 to Telegram
    }
});

// Generate a link code (user must be authenticated)
router.post('/telegram/link-code', requireAuth, async (req, res) => {
    try {
        const code = createLinkCode(req.user.id);
        res.json({ code, expiresIn: '10 minutes' });
    } catch (err) {
        res.status(500).json({ error: 'Failed to generate link code' });
    }
});

// Check Telegram link status
router.get('/telegram/status', requireAuth, async (req, res) => {
    try {
        const profile = await getProfile(req.user.id, req.token);
        res.json({
            linked: !!profile?.telegram_chat_id,
            username: profile?.telegram_username || null,
            linkedAt: profile?.telegram_linked_at || null
        });
    } catch (err) {
        res.status(500).json({ error: 'Failed to check status' });
    }
});

// Unlink Telegram
router.post('/telegram/unlink', requireAuth, async (req, res) => {
    try {
        const admin = getAdminClient();
        await admin
            .from('profiles')
            .update({ telegram_chat_id: null, telegram_username: null, telegram_linked_at: null })
            .eq('id', req.user.id);
        res.json({ success: true });
    } catch (err) {
        res.status(500).json({ error: 'Failed to unlink' });
    }
});

module.exports = router;
