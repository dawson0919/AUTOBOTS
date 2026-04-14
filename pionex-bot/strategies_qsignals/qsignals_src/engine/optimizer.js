// Optimization script for MACD + MA Filter - Wide Search
const { getCandleData } = require('./dataFetcher');
const Backtester = require('./backtester');
const indicators = require('./indicators');
const { createStrategy } = require('./strategies/macdMa');

async function runOptimization() {
    console.log('🔍 Starting Wide-Search MACD + MA Optimization...');

    const symbol = 'BTCUSDT';
    const timeframe = '4h';
    const candles = await getCandleData(symbol, timeframe);
    const closes = candles.map(c => c.close);

    // Expanded Parameter Ranges
    const fastRange = [8, 12, 16, 20, 24];
    const slowRange = [26, 30, 40, 50, 60];
    const signalRange = [7, 9, 12];
    const maRange = [20, 50, 100, 200];

    // 5 * 5 * 3 * 4 = 300 combinations. I'll add more MA range.
    const maRangeExtended = [10, 20, 30, 50, 100, 150, 200, 250];
    // 5 * 5 * 3 * 8 = 600 combinations.

    const backtester = new Backtester({
        initialCapital: 10000,
        positionSize: 0.95,
        commission: 0.0006
    });

    let results = [];
    const customSma = {};
    maRangeExtended.forEach(period => { customSma[period] = indicators.sma(closes, period); });
    const customMacd = {};

    for (const fast of fastRange) {
        for (const slow of slowRange) {
            if (fast >= slow) continue;
            for (const signal of signalRange) {
                const key = `${fast}_${slow}_${signal}`;
                customMacd[key] = indicators.macd(closes, fast, slow, signal);

                for (const ma of maRangeExtended) {
                    const strategyFn = createStrategy({ fast, slow, signal, ma });

                    const wrappedStrategy = (c, id, i, ind) => {
                        const extendedId = { ...id, customMacd, customSma };
                        return strategyFn(c, extendedId, i, ind);
                    };

                    const result = backtester.run(wrappedStrategy, candles);

                    results.push({
                        roi: result.summary.totalReturn,
                        pf: result.summary.profitFactor,
                        winRate: result.summary.winRate,
                        trades: result.summary.totalTrades,
                        params: { fast, slow, signal, ma }
                    });
                }
            }
        }
    }

    results.sort((a, b) => b.roi - a.roi);

    console.log('\n🏆 Top 10 Optimized Combinations (ROI Focused):');
    results.slice(0, 10).forEach((r, idx) => {
        console.log(`${idx + 1}. ROI: ${r.roi.toFixed(2)}% | Trades: ${r.trades} | PF: ${r.pf} | Params: ${JSON.stringify(r.params)}`);
    });

    return results[0];
}

runOptimization().catch(console.error);
