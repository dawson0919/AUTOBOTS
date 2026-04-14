/**
 * Signal Monitor Service
 * Runs after each candle close to detect new trading signals
 * and notify subscribed users via Telegram.
 */
const Backtester = require('../engine/backtester');
const { getCandleData } = require('../engine/dataFetcher');
const { sendSignalNotification, sendCloseSignalNotification, sendHoldingSignalNotification } = require('./telegramBot');

// In-memory cache of last known signals per strategy+symbol
const lastSignals = {};

// All strategies to monitor
function getStrategies() {
    const strategyModules = [
        require('../engine/strategies/ma60'),
        require('../engine/strategies/dualEma'),
        require('../engine/strategies/threeStyle'),
        require('../engine/strategies/turtleBreakout'),
        require('../engine/strategies/macdMa'),
        require('../engine/strategies/granville_eth_4h'),
        require('../engine/strategies/dualSuperTrend'),
        require('../engine/strategies/donchianTrend'),
    ];
    const map = {};
    strategyModules.forEach(s => {
        if (s && s.id) map[s.id] = s;
    });
    return map;
}

// Symbol-strategy-timeframe combos to monitor
const MONITOR_COMBOS = [
    // BTC
    { symbol: 'BTCUSDT', strategyId: 'ma60', timeframe: '4h' },
    { symbol: 'BTCUSDT', strategyId: 'dual_ema', timeframe: '4h' },
    { symbol: 'BTCUSDT', strategyId: 'turtle_breakout', timeframe: '4h' },
    { symbol: 'BTCUSDT', strategyId: 'macd_ma_optimized', timeframe: '4h' },
    { symbol: 'BTCUSDT', strategyId: 'granville_eth_4h', timeframe: '4h' },
    { symbol: 'BTCUSDT', strategyId: 'dual_st_breakout', timeframe: '4h' },
    // ETH
    { symbol: 'ETHUSDT', strategyId: 'ma60', timeframe: '4h' },
    { symbol: 'ETHUSDT', strategyId: 'dual_ema', timeframe: '4h' },
    { symbol: 'ETHUSDT', strategyId: 'turtle_breakout', timeframe: '4h' },
    { symbol: 'ETHUSDT', strategyId: 'macd_ma_optimized', timeframe: '4h' },
    { symbol: 'ETHUSDT', strategyId: 'granville_eth_4h', timeframe: '4h' },
    { symbol: 'ETHUSDT', strategyId: 'dual_st_breakout', timeframe: '4h' },
    // SOL
    { symbol: 'SOLUSDT', strategyId: 'ma60', timeframe: '4h' },
    { symbol: 'SOLUSDT', strategyId: 'dual_ema', timeframe: '4h' },
    { symbol: 'SOLUSDT', strategyId: 'turtle_breakout', timeframe: '4h' },
    { symbol: 'SOLUSDT', strategyId: 'macd_ma_optimized', timeframe: '4h' },
    { symbol: 'SOLUSDT', strategyId: 'dual_st_breakout', timeframe: '4h' },
    // XAU (Gold) - Standardized on XAUUSDT and XAUTUSDT is covered by variations
    { symbol: 'XAUUSDT', strategyId: 'three_style', timeframe: '1h' },
    { symbol: 'XAUUSDT', strategyId: 'three_style', timeframe: '4h' },
    { symbol: 'XAUUSDT', strategyId: 'donchian_trend', timeframe: '4h' },
    { symbol: 'XAUUSDT', strategyId: 'dual_ema', timeframe: '4h' },
    { symbol: 'XAUUSDT', strategyId: 'granville_eth_4h', timeframe: '4h' },
    // PAXG (Spot Gold)
    { symbol: 'PAXGUSDT', strategyId: 'three_style', timeframe: '1h' },
    { symbol: 'PAXGUSDT', strategyId: 'three_style', timeframe: '4h' },
    { symbol: 'PAXGUSDT', strategyId: 'donchian_trend', timeframe: '4h' },
    { symbol: 'PAXGUSDT', strategyId: 'dual_ema', timeframe: '4h' },
    { symbol: 'PAXGUSDT', strategyId: 'granville_eth_4h', timeframe: '4h' },
    { symbol: 'PAXGUSDT', strategyId: 'turtle_breakout', timeframe: '4h' },
    { symbol: 'PAXGUSDT', strategyId: 'ma60', timeframe: '4h' },
    // Indices & Futures (Yahoo Finance: NQ=F ~25000, ES=F ~5800)
    { symbol: 'SPXUSDT', strategyId: 'turtle_breakout', timeframe: '4h' },
    { symbol: 'NQUSDT', strategyId: 'donchian_trend', timeframe: '4h' },
    { symbol: 'ESUSDT', strategyId: 'granville_eth_4h', timeframe: '4h' },
    // WTI Crude Oil (Pionex WTI_USDT_PERP ~$91) — all 8 positive 1h strategies
    { symbol: 'CLUSDT', strategyId: 'dual_ema', timeframe: '1h' },
    { symbol: 'CLUSDT', strategyId: 'donchian_trend', timeframe: '1h' },
    { symbol: 'CLUSDT', strategyId: 'turtle_breakout', timeframe: '1h' },
    { symbol: 'CLUSDT', strategyId: 'ma60', timeframe: '1h' },
    { symbol: 'CLUSDT', strategyId: 'dual_st_breakout', timeframe: '1h' },
    { symbol: 'CLUSDT', strategyId: 'ichimoku_cloud', timeframe: '1h' },
    { symbol: 'CLUSDT', strategyId: 'granville_eth_4h', timeframe: '1h' },
    { symbol: 'CLUSDT', strategyId: 'three_style', timeframe: '1h' },
];

/**
 * Run a single backtest and check for new signals (entry + close)
 */
async function checkSignal(combo, strategiesMap) {
    const { symbol, strategyId, timeframe } = combo;
    const s = strategiesMap[strategyId];
    if (!s) return null;

    try {
        const candles = await getCandleData(symbol, timeframe, { daysBack: 30 });
        if (!candles || candles.length < 50) return null;

        const bt = new Backtester({ initialCapital: 10000 });
        const params = { ...s.defaultParams, symbol, timeframe };
        const strategyFn = s.createStrategy ? s.createStrategy(params) : s.execute;

        const result = bt.run(strategyFn, candles);
        if (!result.recentTrades || result.recentTrades.length === 0) return null;

        const latest = result.recentTrades[0];
        const signalKey = `${strategyId}_${symbol}_${timeframe}`;
        const closeKey = `${signalKey}_CLOSE`;
        const oldSignal = lastSignals[signalKey];
        const oldClose = lastSignals[closeKey];
        const now = Date.now();
        const MAX_AGE_MS = 8 * 60 * 60 * 1000; // 8 hours (2x 4h candle)
        const signals = []; // Collect both entry and close signals

        // ── 1. Check for NEW ENTRY signal (priority: notify on open) ──
        const isNew = !oldSignal ||
            new Date(oldSignal.entryTime).getTime() !== new Date(latest.entryTime).getTime() ||
            oldSignal.type !== latest.type;

        const signalTime = new Date(latest.entryTime).getTime();
        const isRecent = !isNaN(signalTime) && (now - signalTime) < MAX_AGE_MS;

        // Always update entry cache
        lastSignals[signalKey] = {
            entryTime: latest.entryTime,
            type: latest.type,
            entryPrice: latest.entryPrice,
            rule: latest.rule
        };

        // Always notify on new entry — even if trade already closed
        if (isNew && isRecent) {
            signals.push({
                signalType: 'entry',
                strategyId,
                strategyName: s.name,
                symbol,
                timeframe,
                type: latest.type,
                price: latest.entryPrice,
                rule: latest.rule,
                entryTime: latest.entryTime
            });
        }

        // ── 2. Check for NEW CLOSE signal ──
        // Important: result.recentTrades may contain a trade that the backtester "closed" 
        // at the very last candle just for reporting. We must filter these out.
        const lastCandle = candles[candles.length - 1];
        const lastCandleTime = new Date(lastCandle.open_time || lastCandle.openTime).getTime();

        const latestClosed = result.recentTrades.find(t => {
            if (!t.exitTime || !t.exitPrice) return false;
            // Real exit: exit time is BEFORE the latest candle open time
            // Or if it's the exact same time, but it's a known strategy signal (not a forced close)
            return new Date(t.exitTime).getTime() < lastCandleTime;
        });

        if (latestClosed) {
            // ... (rest of close logic)
            const holdMs = new Date(latestClosed.exitTime).getTime() - new Date(latestClosed.entryTime).getTime();
            const intervalMs = (timeframe === '1h' ? 1 : 4) * 60 * 60 * 1000;
            const isZeroHold = holdMs <= intervalMs && Math.abs(latestClosed.pnlPercent || 0) < 0.01;

            const isNewClose = !oldClose ||
                new Date(oldClose.exitTime).getTime() !== new Date(latestClosed.exitTime).getTime();
            const closeTime = new Date(latestClosed.exitTime).getTime();
            const isRecentClose = !isNaN(closeTime) && (now - closeTime) < MAX_AGE_MS;

            // Always update close cache
            lastSignals[closeKey] = {
                exitTime: latestClosed.exitTime,
                exitPrice: latestClosed.exitPrice,
                entryPrice: latestClosed.entryPrice,
                type: latestClosed.type,
                pnlPercent: latestClosed.pnlPercent
            };

            if (isNewClose && isRecentClose && !isZeroHold) {
                signals.push({
                    signalType: 'close',
                    strategyId,
                    strategyName: s.name,
                    symbol,
                    timeframe,
                    type: latestClosed.type,
                    entryPrice: latestClosed.entryPrice,
                    exitPrice: latestClosed.exitPrice,
                    entryTime: latestClosed.entryTime,
                    exitTime: latestClosed.exitTime,
                    pnlPercent: latestClosed.pnlPercent
                });
            }
        }

        // ── 3. Check for HOLDING status (requested update) ──
        // [SUPPRESSED] Users no longer want periodic holding notifications
        /*
        const currentlyOpen = result.recentTrades.find(t => {
            if (!t.exitTime) return true; // Truly open
            return new Date(t.exitTime).getTime() >= lastCandleTime; // Forced close at last candle
        });

        if (currentlyOpen) {
            // We report this as 'holding' every interval
            // To prevent spamming, we can use a cache key for the holding status per hour
            const hourKey = new Date().toISOString().substring(0, 13); // e.g. "2024-02-28T06"
            const holdNotifyKey = `${signalKey}_HOLD_${hourKey}`;

            if (!lastSignals[holdNotifyKey]) {
                lastSignals[holdNotifyKey] = true; // Mark as notified for this hour
                signals.push({
                    signalType: 'holding',
                    strategyId,
                    strategyName: s.name,
                    symbol,
                    timeframe,
                    type: currentlyOpen.type,
                    entryPrice: currentlyOpen.entryPrice,
                    currentPrice: lastCandle.close,
                    entryTime: currentlyOpen.entryTime,
                    pnlPercent: ((lastCandle.close - currentlyOpen.entryPrice) / currentlyOpen.entryPrice) * (currentlyOpen.type === 'LONG' ? 100 : -100)
                });
            }
        }
        */

        return signals.length > 0 ? signals : null;
    } catch (err) {
        console.error(`[SignalMonitor] Error checking ${strategyId}/${symbol}:`, err.message);
        return null;
    }
}

/**
 * Notify all subscribers of a signal (entry or close) via Telegram
 */
async function notifySubscribers(signal, supabaseAdmin) {
    try {
        // Find all subscriptions for this strategy+symbol+timeframe
        // Handle symbol variations (XAUUSDT vs XAU/USDT)
        const symbolVariations = [signal.symbol];
        if (signal.symbol === 'XAUUSDT') symbolVariations.push('XAU/USDT', 'GOLD', 'XAUTUSDT', 'Tether Gold');

        const { data: subs, error } = await supabaseAdmin
            .from('subscriptions')
            .select('user_id')
            .eq('strategy_id', signal.strategyId)
            .in('symbol', symbolVariations)
            .eq('timeframe', signal.timeframe);

        if (error || !subs || subs.length === 0) return 0;

        // Get Telegram chat IDs for these users
        const userIds = [...new Set(subs.map(s => s.user_id))];
        const { data: profiles } = await supabaseAdmin
            .from('profiles')
            .select('id, telegram_chat_id')
            .in('id', userIds)
            .not('telegram_chat_id', 'is', null);

        if (!profiles || profiles.length === 0) return 0;

        let sent = 0;
        for (const profile of profiles) {
            let result;
            if (signal.signalType === 'close') {
                result = await sendCloseSignalNotification(profile.telegram_chat_id, signal);
            } else {
                result = await sendSignalNotification(profile.telegram_chat_id, signal);
            }
            if (result) sent++;
        }

        const label = signal.signalType === 'close' ? '🔓 CLOSE' : '🔔 ENTRY';
        console.log(`[SignalMonitor] ${label} Notified ${sent}/${profiles.length} users for ${signal.strategyId}/${signal.symbol}`);
        return sent;
    } catch (err) {
        console.error('[SignalMonitor] Notify error:', err.message);
        return 0;
    }
}

/**
 * Sync in-memory cache with database to prevent redundant notifications on restart
 */
async function syncLastSignals(supabaseAdmin) {
    if (Object.keys(lastSignals).length > 0) return; // Already hydrated

    try {
        const { data, error } = await supabaseAdmin
            .from('subscriptions')
            .select('strategy_id, symbol, timeframe, latest_signal')
            .not('latest_signal', 'is', null);

        if (error || !data) return;

        for (const sub of data) {
            const key = `${sub.strategy_id}_${sub.symbol}_${sub.timeframe}`;
            if (!lastSignals[key] && sub.latest_signal) {
                lastSignals[key] = {
                    entryTime: sub.latest_signal.time,
                    type: sub.latest_signal.type,
                    entryPrice: sub.latest_signal.price,
                    rule: sub.latest_signal.rule
                };
            }
            // Also hydrate close cache if available
            const closeKey = `${key}_CLOSE`;
            if (!lastSignals[closeKey] && sub.latest_signal && sub.latest_signal.close_time) {
                lastSignals[closeKey] = {
                    exitTime: sub.latest_signal.close_time,
                    exitPrice: sub.latest_signal.close_price,
                    entryPrice: sub.latest_signal.price,
                    type: sub.latest_signal.type,
                    pnlPercent: sub.latest_signal.close_pnl
                };
            }
        }
        console.log(`[SignalMonitor] Hydrated cache with ${Object.keys(lastSignals).length} existing signals from DB`);
    } catch (err) {
        console.error('[SignalMonitor] Sync error:', err.message);
    }
}

/**
 * Get symbol variations for dedup
 */
function getSymbolVariations(symbol) {
    const variations = [symbol];
    if (symbol === 'XAUUSDT' || symbol === 'PAXGUSDT') {
        variations.push('XAU/USDT', 'GOLD', 'XAUTUSDT', 'Tether Gold', 'PAXGUSDT', 'Pax Gold');
    }
    return variations;
}

/**
 * Run the full signal check cycle
 */
async function runSignalCheck(supabaseAdmin) {
    await syncLastSignals(supabaseAdmin);

    const strategies = getStrategies();
    const startTime = Date.now();
    let newEntrySignals = 0;
    let newCloseSignals = 0;

    console.log(`[SignalMonitor] Starting signal check (${MONITOR_COMBOS.length} combos)...`);

    for (const combo of MONITOR_COMBOS) {
        const signals = await checkSignal(combo, strategies);
        if (!signals || signals.length === 0) continue;

        for (const signal of signals) {
            const symbolVariations = getSymbolVariations(signal.symbol);

            if (signal.signalType === 'entry') {
                // ── Entry Signal: DB dedup + notify ──
                try {
                    const { data: existing } = await supabaseAdmin
                        .from('subscriptions')
                        .select('latest_signal')
                        .eq('strategy_id', signal.strategyId)
                        .in('symbol', symbolVariations)
                        .not('latest_signal', 'is', null)
                        .limit(5);

                    const alreadyProcessed = (existing || []).some(sub => {
                        if (!sub.latest_signal) return false;
                        const dbTime = new Date(sub.latest_signal.time).getTime();
                        const sigTime = new Date(signal.entryTime).getTime();
                        return dbTime === sigTime && sub.latest_signal.type === signal.type;
                    });

                    if (alreadyProcessed) continue;
                } catch (err) {
                    console.warn('[SignalMonitor] Atomic check failed, proceeding:', err.message);
                }

                newEntrySignals++;
                console.log(`[SignalMonitor] 🔔 NEW ENTRY: ${signal.type} ${signal.symbol} @ $${signal.price} (${signal.strategyName})`);

                // Update DB
                try {
                    await supabaseAdmin
                        .from('subscriptions')
                        .update({
                            latest_signal: {
                                type: signal.type,
                                price: signal.price,
                                time: signal.entryTime,
                                rule: signal.rule,
                                strategyName: signal.strategyName,
                                processed_at: new Date().toISOString()
                            }
                        })
                        .eq('strategy_id', signal.strategyId)
                        .in('symbol', symbolVariations)
                        .eq('timeframe', signal.timeframe);
                } catch (err) {
                    console.error('[SignalMonitor] DB update error:', err.message);
                }

                await notifySubscribers(signal, supabaseAdmin);

            } else if (signal.signalType === 'close') {
                // ── Close Signal: DB dedup + notify ──
                try {
                    const { data: existing } = await supabaseAdmin
                        .from('subscriptions')
                        .select('latest_signal')
                        .eq('strategy_id', signal.strategyId)
                        .in('symbol', symbolVariations)
                        .not('latest_signal', 'is', null)
                        .limit(5);

                    const alreadyProcessed = (existing || []).some(sub => {
                        if (!sub.latest_signal || !sub.latest_signal.close_time) return false;
                        const dbCloseTime = new Date(sub.latest_signal.close_time).getTime();
                        const sigCloseTime = new Date(signal.exitTime).getTime();
                        return dbCloseTime === sigCloseTime;
                    });

                    if (alreadyProcessed) continue;
                } catch (err) {
                    console.warn('[SignalMonitor] Close atomic check failed, proceeding:', err.message);
                }

                newCloseSignals++;
                console.log(`[SignalMonitor] 🔓 CLOSE: ${signal.type} ${signal.symbol} @ $${signal.exitPrice} PnL:${signal.pnlPercent.toFixed(2)}% (${signal.strategyName})`);

                // Update DB with close info
                try {
                    await supabaseAdmin
                        .from('subscriptions')
                        .update({
                            latest_signal: {
                                type: signal.type,
                                price: signal.entryPrice,
                                time: signal.entryTime,
                                strategyName: signal.strategyName,
                                close_price: signal.exitPrice,
                                close_time: signal.exitTime,
                                close_pnl: signal.pnlPercent,
                                processed_at: new Date().toISOString()
                            }
                        })
                        .eq('strategy_id', signal.strategyId)
                        .in('symbol', symbolVariations)
                        .eq('timeframe', signal.timeframe);
                } catch (err) {
                    console.error('[SignalMonitor] Close DB update error:', err.message);
                }
                await notifySubscribers(signal, supabaseAdmin);
            }
            // 'holding' logic removed as per user request
        }
    }

    const duration = ((Date.now() - startTime) / 1000).toFixed(1);
    console.log(`[SignalMonitor] Check complete: ${newEntrySignals} entry + ${newCloseSignals} close signals (${duration}s)`);
}

/**
 * Start the signal monitor with scheduled intervals
 */
let monitorInterval = null;

function startSignalMonitor(supabaseAdmin) {
    if (!supabaseAdmin) {
        console.error('[SignalMonitor] No Supabase admin client provided');
        return;
    }

    if (monitorInterval) {
        console.warn('[SignalMonitor] monitor already running, clearing old interval...');
        stopSignalMonitor();
    }

    // Aligned check: start at the next top of the hour + 30 seconds
    const now = new Date();
    const nextHour = new Date(now);
    nextHour.setHours(now.getHours() + 1, 0, 30, 0);
    const msUntilNext = nextHour.getTime() - now.getTime();

    console.log(`[SignalMonitor] Initial check scheduled in ${(msUntilNext / 1000 / 60).toFixed(1)} mins (aligned to ${nextHour.toLocaleTimeString()})`);

    // First aligned check
    setTimeout(() => {
        runSignalCheck(supabaseAdmin);

        // Then check every 1 hour (exactly 3,600,000 ms)
        monitorInterval = setInterval(() => {
            runSignalCheck(supabaseAdmin);
        }, 60 * 60 * 1000);

        console.log('[SignalMonitor] Interval started (checking every 60 min)');
    }, msUntilNext);
}

function stopSignalMonitor() {
    if (monitorInterval) {
        clearInterval(monitorInterval);
        monitorInterval = null;
    }
}

module.exports = {
    startSignalMonitor,
    stopSignalMonitor,
    runSignalCheck,
    MONITOR_COMBOS
};
