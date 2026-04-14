// Premium Strategy: Dual EMA 4H Optimized v2
// EMA 雙均線交叉策略 + RSI 過濾 + 停損/停利與冷卻機制

const pineScript = `
//@version=5
strategy("Dual EMA 4H Optimized v2", overlay=true, default_qty_type=strategy.percent_of_equity, default_qty_value=100, commission_type=strategy.commission.percent, commission_value=0.06, initial_capital=100, pyramiding=0)

fast_len      = input.int(50,    "Fast EMA Period")
slow_len      = input.int(200,   "Slow EMA Period")
use_rsi_filter = input.bool(true,  "Enable RSI Filter")
rsi_len        = input.int(14,     "RSI Period")
rsi_thresh     = input.int(50,     "RSI Threshold")
sl_pct         = input.float(5.0,  "Stop Loss %")
tp_pct         = input.float(0.0,  "Take Profit % (0 to disable)")
cooldown_bars  = input.int(3,      "Cooldown Bars After SL")

fast_ema = ta.ema(close, fast_len)
slow_ema = ta.ema(close, slow_len)
rsi_val  = ta.rsi(close, rsi_len)

longCondition  = ta.crossover(fast_ema, slow_ema) and (not use_rsi_filter or rsi_val > rsi_thresh)
shortCondition = ta.crossunder(fast_ema, slow_ema) and (not use_rsi_filter or rsi_val < (100 - rsi_thresh))

if (longCondition)
    strategy.entry("Long", strategy.long)
if (shortCondition)
    strategy.entry("Short", strategy.short)

if (strategy.position_size > 0)
    strategy.exit("Exit Long", "Long", stop=strategy.position_avg_price * (1 - sl_pct/100), limit=tp_pct > 0 ? strategy.position_avg_price * (1 + tp_pct/100) : na)
if (strategy.position_size < 0)
    strategy.exit("Exit Short", "Short", stop=strategy.position_avg_price * (1 + sl_pct/100), limit=tp_pct > 0 ? strategy.position_avg_price * (1 - tp_pct/100) : na)
`.trim();

/**
 * Optimized Parameters Matrix (derived from grid search)
 * Target: ROI > 20% across all pairs.
 * Summary (4H timeframe, 180d, Spot):
 * ETH: ~118% ROI
 * SOL: ~115% ROI
 * GOLD: ~30% ROI
 */
const OPTIMIZED_PARAMS = {
    'ETHUSDT': { fastLen: 5, slowLen: 50, slPct: 0.05, tpPct: 0.1, rsiThresh: 50, cooldown: 3, useRsi: false },
    'SOLUSDT': { fastLen: 30, slowLen: 100, slPct: 0.03, tpPct: 0.1, rsiThresh: 45, cooldown: 3, useRsi: true },
    'XAUUSDT': { fastLen: 30, slowLen: 50, slPct: 0.01, tpPct: 0.25, rsiThresh: 40, cooldown: 3, useRsi: true },
    'BTCUSDT': { fastLen: 15, slowLen: 100, slPct: 0.05, tpPct: 0.25, rsiThresh: 50, cooldown: 3, useRsi: true },
    // CLUSDT (WTI Crude Oil) — Pionex data, re-optimized 2026-04
    'CLUSDT': { fastLen: 10, slowLen: 30, slPct: 0.07, tpPct: 0.15, rsiThresh: 55, cooldown: 3, useRsi: true },      // 1h: ROI 39.75%
    'CLUSDT_4h': { fastLen: 5, slowLen: 75, slPct: 0.07, tpPct: 0.2, rsiThresh: 40, cooldown: 3, useRsi: true }     // 4h: ROI 18.82%
};

function createStrategy(params = {}) {
    const symbol = params.symbol || 'BTCUSDT';

    const fast_len = params.fastLen;
    const slow_len = params.slowLen;
    const rsi_len = 14;
    const rsi_thresh = params.rsiThresh;
    const sl_pct = params.slPct;
    const tp_pct = params.tpPct;
    const cooldown = params.cooldown;
    const useRsi = params.useRsi;

    let position = null;
    let entryPrice = 0;
    let lastSLBar = -999;

    return function execute(candles, indicatorData, i, indicators) {
        if (!indicatorData.ema[fast_len]) indicatorData.ema[fast_len] = indicators.ema(indicatorData.close, fast_len);
        if (!indicatorData.ema[slow_len]) indicatorData.ema[slow_len] = indicators.ema(indicatorData.close, slow_len);
        if (useRsi && !indicatorData.rsi[rsi_len]) indicatorData.rsi[rsi_len] = indicators.rsi(indicatorData.close, rsi_len);

        const close = indicatorData.close;
        const fastEma = indicatorData.ema[fast_len];
        const slowEma = indicatorData.ema[slow_len];
        const rsi = useRsi ? indicatorData.rsi[rsi_len] : null;

        if (!fastEma || !slowEma || !fastEma[i] || !slowEma[i]) return null;
        if (useRsi && (!rsi || !rsi[i])) return null;

        // 1. Exit Logic (SL/TP)
        if (position === 'LONG') {
            if (close[i] <= entryPrice * (1 - sl_pct)) {
                position = null; lastSLBar = i; return 'CLOSE_LONG';
            }
            if (tp_pct > 0 && close[i] >= entryPrice * (1 + tp_pct)) {
                position = null; return 'CLOSE_LONG';
            }
        } else if (position === 'SHORT') {
            if (close[i] >= entryPrice * (1 + sl_pct)) {
                position = null; lastSLBar = i; return 'CLOSE_SHORT';
            }
            if (tp_pct > 0 && close[i] <= entryPrice * (1 - tp_pct)) {
                position = null; return 'CLOSE_SHORT';
            }
        }

        // 2. Cooldown
        if (i - lastSLBar <= cooldown) return null;

        // 3. Entry Logic
        const longCond = fastEma[i - 1] <= slowEma[i - 1] && fastEma[i] > slowEma[i] && (!useRsi || rsi[i] > rsi_thresh);
        const shortCond = fastEma[i - 1] >= slowEma[i - 1] && fastEma[i] < slowEma[i] && (!useRsi || rsi[i] < (100 - rsi_thresh));

        if (longCond) {
            position = 'LONG';
            entryPrice = close[i];
            return 'BUY';
        }

        if (shortCond) {
            position = 'SHORT';
            entryPrice = close[i];
            return 'SELL';
        }

        return null;
    };
}

const defaultParams = {
    fastLen: 50, slowLen: 200, rsiLen: 14, rsiThresh: 50, slPct: 0.05, tpPct: 0.0, cooldown: 3, useRsi: true
};

module.exports = {
    id: 'dual_ema',
    name: 'Dual EMA 4H Optimized v2',
    description: 'EMA 雙均線交叉策略。已針對 ETH, SOL, GOLD 進行深度優化，全面突破 20% ROI。',
    category: 'Premium',
    author: 'QuantSignal',
    pineScript,
    createStrategy: (params = {}) => {
        const symbol = (params.symbol || 'BTCUSDT').toUpperCase().replace(/[^A-Z0-9]/g, '');
        const timeframe = params.timeframe || '4h';
        const opt = OPTIMIZED_PARAMS[`${symbol}_${timeframe}`] || OPTIMIZED_PARAMS[symbol] || OPTIMIZED_PARAMS['BTCUSDT'];

        const finalParams = { ...defaultParams, ...opt };

        for (const key in params) {
            if (params[key] !== undefined && params[key] !== defaultParams[key]) {
                finalParams[key] = params[key];
            }
        }
        finalParams.symbol = symbol;
        finalParams.timeframe = params.timeframe || '4h';

        return createStrategy(finalParams);
    },
    execute: createStrategy(defaultParams),
    defaultParams
};
