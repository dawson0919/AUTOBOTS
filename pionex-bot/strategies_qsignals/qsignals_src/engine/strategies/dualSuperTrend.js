const pineScript = `
//@version=5
strategy("雙 ST 突破 (Dual ST Breakout)", overlay=true)

st1_period = input(10, "ST1 Period")
st1_mult = input(3.0, "ST1 Multiplier")
st2_period = input(20, "ST2 Period")
st2_mult = input(5.0, "ST2 Multiplier")
donchian_period = input(15, "Donchian Period")

[st1_val, st1_dir] = ta.supertrend(st1_period, st1_mult)
[st2_val, st2_dir] = ta.supertrend(st2_period, st2_mult)

upper = ta.highest(high, donchian_period)[1]
lower = ta.lowest(low, donchian_period)[1]

if (st1_dir < 0 and st2_dir < 0 and close > upper)
    strategy.entry("Long", strategy.long)
if (st1_dir > 0 and st2_dir > 0 and close < lower)
    strategy.entry("Short", strategy.short)
if (strategy.position_size > 0 and st1_dir > 0)
    strategy.close("Long")
if (strategy.position_size < 0 and st1_dir < 0)
    strategy.close("Short")
`;

// Optimized parameters per symbol/timeframe (based on grid search optimization)
const OPTIMIZED_PARAMS = {
    'ETHUSDT_1h': { st1_period: 10, st1_mult: 3.0, st2_period: 20, st2_mult: 5.0, donchian_period: 15 },
    'ETHUSDT_4h': { st1_period: 7, st1_mult: 2.5, st2_period: 30, st2_mult: 5.0, donchian_period: 15 },
    'SOLUSDT_1h': { st1_period: 10, st1_mult: 3.0, st2_period: 20, st2_mult: 4.0, donchian_period: 20 },
    'SOLUSDT_4h': { st1_period: 14, st1_mult: 2.0, st2_period: 30, st2_mult: 5.0, donchian_period: 15 },
    'BTCUSDT_1h': { st1_period: 14, st1_mult: 3.0, st2_period: 30, st2_mult: 3.0, donchian_period: 15 },
    'BTCUSDT_4h': { st1_period: 14, st1_mult: 2.5, st2_period: 14, st2_mult: 5.0, donchian_period: 15 },
    'XAUUSDT_4h': { st1_period: 10, st1_mult: 3.0, st2_period: 20, st2_mult: 4.0, donchian_period: 20 },
    'CLUSDT_1h': { st1_period: 7, st1_mult: 2.0, st2_period: 14, st2_mult: 3.0, donchian_period: 10 },
};

const DEFAULT_PARAMS = { st1_period: 10, st1_mult: 3.0, st2_period: 20, st2_mult: 4.0, donchian_period: 20 };

function createStrategy(params = {}) {
    const ST1_PERIOD = params.st1_period || DEFAULT_PARAMS.st1_period;
    const ST1_MULT = params.st1_mult || DEFAULT_PARAMS.st1_mult;
    const ST2_PERIOD = params.st2_period || DEFAULT_PARAMS.st2_period;
    const ST2_MULT = params.st2_mult || DEFAULT_PARAMS.st2_mult;
    const DC_PERIOD = params.donchian_period || DEFAULT_PARAMS.donchian_period;

    return function execute(candles, indicatorData, i, indicators) {
        // Cache key includes params to avoid collision when switching
        const cacheKey = `dualSt_${ST1_PERIOD}_${ST1_MULT}_${ST2_PERIOD}_${ST2_MULT}_${DC_PERIOD}`;

        if (!indicatorData[cacheKey]) {
            const closes = candles.map(c => c.close);
            const highs = candles.map(c => c.high);
            const lows = candles.map(c => c.low);

            indicatorData[cacheKey] = {
                st1: indicators.superTrend(highs, lows, closes, ST1_PERIOD, ST1_MULT),
                st2: indicators.superTrend(highs, lows, closes, ST2_PERIOD, ST2_MULT),
                dc: indicators.donchian(highs, lows, DC_PERIOD)
            };
        }

        const cache = indicatorData[cacheKey];
        const st1 = cache.st1;
        const st2 = cache.st2;
        const dc = cache.dc;

        // SuperTrend direction: 1 = Up (bullish), -1 = Down (bearish)
        const trendUp = st1.direction[i] === 1 && st2.direction[i] === 1;
        const trendDown = st1.direction[i] === -1 && st2.direction[i] === -1;

        const upperPrev = dc.upper[i - 1];
        const lowerPrev = dc.lower[i - 1];

        // Entry: Both ST aligned + Donchian breakout
        if (trendUp && upperPrev && candles[i].close > upperPrev) {
            return 'BUY';
        }
        if (trendDown && lowerPrev && candles[i].close < lowerPrev) {
            return 'SELL';
        }

        // Exit: Fast ST (ST1) flips direction
        if (st1.direction[i] === -1) {
            return 'CLOSE_LONG';
        }
        if (st1.direction[i] === 1) {
            return 'CLOSE_SHORT';
        }

        return null;
    };
}

// Default execute for route registration (uses default params)
const defaultExecute = createStrategy(DEFAULT_PARAMS);

module.exports = {
    id: 'dual_st_breakout',
    name: '雙 ST 突破',
    description: 'SuperTrend 共振 + 唐奇安通道突破策略。雙重 SuperTrend 確認趨勢方向，結合唐奇安通道突破進場，ST1 翻轉作為動態出場。',
    category: 'Premium',
    author: 'Q-Signals Lab',
    pineScript,
    defaultParams: DEFAULT_PARAMS,
    execute: defaultExecute,
    createStrategy: (params) => {
        // Select optimized params based on symbol/timeframe
        // Optimized params must override defaults (params contains defaultParams from routes.js)
        const key = `${params.symbol}_${params.timeframe}`;
        const optimized = OPTIMIZED_PARAMS[key] || DEFAULT_PARAMS;
        const merged = { ...params, ...optimized }; // optimized overrides defaults
        return createStrategy(merged);
    }
};
