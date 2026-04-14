// Strategy: Ichimoku Cloud Breakout with EMA Trend Filter
// Buy when price > cloud, Tenkan > Kijun, and price > EMA 200
// Sell when price < cloud, Tenkan < Kijun, and price < EMA 200

const pineScript = `
//@version=5
strategy("Ichimoku Cloud with Trend Filter", overlay=true)

tenkan_len = input.int(9, "Tenkan Period")
kijun_len = input.int(26, "Kijun Period")
senkou_b_len = input.int(52, "Senkou B Period")
ema_len = input.int(200, "EMA Filter Period")

[tenkan, kijun, senkouA, senkouB, _] = ta.ichimoku(tenkan_len, kijun_len, senkou_b_len)
ema = ta.ema(close, ema_len)

cloud_top = math.max(senkouA[26], senkouB[26])
cloud_bottom = math.min(senkouA[26], senkouB[26])

longCondition = close > cloud_top and tenkan > kijun and close > ema
shortCondition = close < cloud_bottom and tenkan < kijun and close < ema

if (longCondition)
    strategy.entry("Long", strategy.long)

if (shortCondition)
    strategy.entry("Short", strategy.short)
`.trim();

function createStrategy(params = {}) {
    let p1 = params.p1 || 9;
    let p2 = params.p2 || 26;
    let p3 = params.p3 || 52;
    let emaPeriod = params.ema_period || 200;

    // Per-symbol/timeframe optimized parameter overrides
    if (params.symbol === 'SOLUSDT' && params.timeframe === '4h') {
        p1 = 24; p2 = 20; p3 = 44; emaPeriod = 200;
    } else if (params.symbol === 'ETHUSDT' && params.timeframe === '4h') {
        p1 = 24; p2 = 32; p3 = 80; emaPeriod = 100;
    } else if (params.symbol === 'BTCUSDT' && params.timeframe === '4h') {
        p1 = 24; p2 = 26; p3 = 80; emaPeriod = 100;
    } else if (params.symbol === 'BTCUSDT' && params.timeframe === '1h') {
        p1 = 12; p2 = 20; p3 = 80; emaPeriod = 100;
    } else if (params.symbol === 'CLUSDT' && params.timeframe === '1h') {
        p1 = 7; p2 = 20; p3 = 44; emaPeriod = 50;
    }

    let cachedIchi = null;

    return function execute(candles, indicatorData, i, indicators) {
        // Use pre-computed EMA from indicatorData
        const emaArr = indicatorData.ema[emaPeriod];
        if (!emaArr) return null;

        // Compute ichimoku once and cache
        if (!cachedIchi) {
            cachedIchi = indicators.ichimoku(indicatorData.high, indicatorData.low, indicatorData.close, p1, p2, p3);
        }

        if (!cachedIchi || i < Math.max(p3 + p2, emaPeriod)) return null;

        const close = candles[i].close;

        const tenkan = cachedIchi.tenkan[i];
        const kijun = cachedIchi.kijun[i];
        const senkouA = cachedIchi.spanA[i];
        const senkouB = cachedIchi.spanB[i];
        const currEma = emaArr[i];

        if (tenkan === null || kijun === null || senkouA === null || senkouB === null || currEma === null) return null;

        const cloudTop = Math.max(senkouA, senkouB);
        const cloudBottom = Math.min(senkouA, senkouB);

        // Long: Price > Cloud AND Tenkan > Kijun AND Price > EMA
        if (close > cloudTop && tenkan > kijun && close > currEma) {
            return 'BUY';
        }

        // Short: Price < Cloud AND Tenkan < Kijun AND Price < EMA
        if (close < cloudBottom && tenkan < kijun && close < currEma) {
            return 'SELL';
        }

        // Exit long: price drops below cloud or tenkan crosses below kijun
        // Exit short: price rises above cloud or tenkan crosses above kijun
        // Only exit when clear reversal signal, otherwise hold
        if (close < cloudBottom || tenkan < kijun) {
            return 'CLOSE_LONG';
        }
        if (close > cloudTop || tenkan > kijun) {
            return 'CLOSE_SHORT';
        }

        return null; // Hold position
    };
}

module.exports = {
    id: 'ichimoku_cloud',
    name: '一目均衡表突破系統',
    description: '結合 Ichimoku Cloud 突破與 EMA 200 長期趨勢濾網。高勝率趨勢跟蹤策略。',
    category: 'Trend',
    author: 'QuantSignal',
    pineScript,
    createStrategy,
    execute: createStrategy({ p1: 9, p2: 26, p3: 52, ema_period: 200 }),
    defaultParams: {
        p1: 9,
        p2: 26,
        p3: 52,
        ema_period: 200
    }
};
