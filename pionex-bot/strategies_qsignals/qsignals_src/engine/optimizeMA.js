const { searchParameters } = require('./parameterSearch');
const { createStrategy } = require('./strategies/ma60');

async function runOptimization() {
    const symbols = ['BTCUSDT'];

    const paramGrid = {
        period: [30, 50, 60, 90, 120],
        filterPeriod: [0, 100, 200, 250]
    };

    for (const symbol of symbols) {
        try {
            const results = await searchParameters({
                createStrategy,
                symbol,
                paramGrid,
                backtestOptions: {
                    initialCapital: 10000,
                    commission: 0.0006
                }
            });
            require('fs').writeFileSync(`optimization_${symbol}.json`, JSON.stringify(results, null, 2));
            console.log(`Saved results to optimization_${symbol}.json`);
        } catch (err) {
            console.error(`Error optimizing ${symbol}:`, err.message);
        }
    }
}

runOptimization();
