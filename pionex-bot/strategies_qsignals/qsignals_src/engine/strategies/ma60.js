// Strategy: 60-period SMA Bidirectional
// Buy when price crosses above 60 SMA, sell when crosses below
// No trend filter - pure price action

const pineScript = `
//@version=5
strategy("MA Bidirectional with Filter", overlay=true)

ma_len = input.int(60, "MA Period")
filter_len = input.int(250, "Filter MA Period")

ma = ta.sma(close, ma_len)
filter_ma = ta.sma(close, filter_len)

longCondition = ta.crossover(close, ma) and (filter_len == 0 or close > filter_ma)
shortCondition = ta.crossunder(close, ma) and (filter_len == 0 or close < filter_ma)

if (longCondition)
    strategy.entry("Long", strategy.long)

if (shortCondition)
    strategy.entry("Short", strategy.short)
`.trim();

function createStrategy(params = {}) {
    let period = params.period || 60;
    let filterPeriod = params.filterPeriod || 250;

    // Apply Optimized Parameters for Gold 4h
    if (params.symbol === 'XAUUSDT' && params.timeframe === '4h') {
        period = 20; filterPeriod = 100;
    }

    // Apply Optimized Parameters for BTC 4h
    if (params.symbol === 'BTCUSDT' && params.timeframe === '4h') {
        period = 20; filterPeriod = 100;
    }

    // Apply Optimized Parameters for ETH 4h
    if (params.symbol === 'ETHUSDT' && params.timeframe === '4h') {
        period = 120; filterPeriod = 250;
    }

    // Apply Optimized Parameters for CLUSDT (WTI Crude Oil) 1h — Pionex 21d/500bars
    if (params.symbol === 'CLUSDT' && params.timeframe === '1h') {
        period = 30; filterPeriod = 100;
        console.log(`[MA60] Init CLUSDT/1h Optimized: period=${period} filter=${filterPeriod}`);
    }

    return function execute(candles, indicatorData, i, indicators) {
        const sma = indicatorData.sma[period];
        // Calculate filter MA on the fly if not pre-calculated, or ensure backtester calculates it
        // The backtester pre-calculates specific periods. We should use those if possible.
        // If filterPeriod is not in pre-calculated list, this might fail or we need to calc manually.
        // For optimization, we'll stick to standard periods available in backtester: [50, 100, 200, 250]

        let filterSma = null;
        if (filterPeriod > 0) {
            filterSma = indicatorData.sma[filterPeriod];
            if (!filterSma) {
                // If not pre-calculated, we can't easily do it here without overhead.
                // Assuming backtester provides it.
                return null;
            }
        }

        if (!sma || sma[i] === null) return null;
        if (filterPeriod > 0 && (filterSma === null || filterSma[i] === null)) return null;

        const close = indicatorData.close;
        const prevClose = close[i - 1];
        const currClose = close[i];
        const prevSma = sma[i - 1];
        const currSma = sma[i];

        const currFilter = filterPeriod > 0 ? filterSma[i] : null;

        // Ensure we have previous data
        if (prevSma === null || prevSma === undefined) return null;

        // Long: Price crosses above MA + Filter Check
        // Previous close was below or equal, current is above
        if (prevClose <= prevSma && currClose > currSma) {
            if (filterPeriod === 0 || currClose > currFilter) {
                return 'BUY';
            }
        }

        // Short: Price crosses below MA + Filter Check
        // Previous close was above or equal, current is below
        if (prevClose >= prevSma && currClose < currSma) {
            if (filterPeriod === 0 || currClose < currFilter) {
                return 'SELL';
            }
        }

        return null;
    };
}

module.exports = {
    id: 'ma60',
    name: '60 MA 雙向策略 (優化版)',
    description: '基於 60 週期 SMA 且加入 250 週期趨勢濾網。順勢而為，過濾震盪。',
    category: 'Basic',
    author: 'QuantSignal',
    pineScript,
    createStrategy,
    execute: createStrategy({ period: 60, filterPeriod: 250 }),
    defaultParams: {
        period: 60,
        filterPeriod: 250
    }
};
