// Reusable Strategy Parameter Optimization Engine
const { getCandleData } = require('./dataFetcher');
const Backtester = require('./backtester');

/**
 * Generic parameter search function.
 * @param {Object} options 
 * @param {Function} options.createStrategy - Strategy factory function (params => executeFn)
 * @param {string} options.symbol - Trading symbol (e.g. 'BTCUSDT')
 * @param {string} options.timeframe - Timeframe (default '4h')
 * @param {Object} options.paramGrid - Object where keys are param names and values are arrays of values to test
 * @param {Object} options.backtestOptions - Options for Backtester (capital, commission, etc.)
 */
async function searchParameters({ createStrategy, symbol, timeframe = '4h', paramGrid, backtestOptions = {} }) {
    console.log(`\n🚀 Starting Parameter Search for ${symbol}...`);

    // 1. Fetch data (DB-only)
    const candles = await getCandleData(symbol, timeframe);
    if (!candles || candles.length < 50) {
        throw new Error(`Insufficient data for ${symbol}. Found ${candles?.length || 0} candles.`);
    }
    console.log(`📊 Loaded ${candles.length} candles from database.`);

    // 2. Initialize Backtester
    const backtester = new Backtester({
        initialCapital: backtestOptions.initialCapital || 10000,
        positionSize: backtestOptions.positionSize || 0.95,
        commission: backtestOptions.commission || 0.0006,
        slippage: backtestOptions.slippage || 0.0005
    });

    // 3. Generate parameter combinations
    const combinations = generateGrid(paramGrid);
    console.log(`📝 Testing ${combinations.length} parameter combinations...`);

    let results = [];
    let best = { roi: -Infinity };

    // 4. Run loop
    for (const params of combinations) {
        const strategyFn = createStrategy(params);
        const result = backtester.run(strategyFn, candles);

        const summary = {
            roi: result.summary.totalReturn,
            plRatio: result.summary.plRatio,
            winRate: result.summary.winRate,
            trades: result.summary.totalTrades,
            params
        };

        results.push(summary);
        if (summary.roi > best.roi) {
            best = summary;
        }
    }

    // 5. Output Results
    results.sort((a, b) => b.roi - a.roi);

    console.log(`\n✅ Optimization Complete for ${symbol}`);
    console.log(`🏆 Best ROI: ${best.roi.toFixed(2)}% | Params: ${JSON.stringify(best.params)}`);
    console.log('\nTop 5 Results:');
    results.slice(0, 5).forEach((r, i) => {
        console.log(`${i + 1}. ROI: ${r.roi.toFixed(2)}% | PL Ratio: ${r.plRatio} | Trades: ${r.trades} | Params: ${JSON.stringify(r.params)}`);
    });

    return { best, top: results.slice(0, 10) };
}

// Private helper to generate cartesian product of parameters
function generateGrid(grid) {
    const keys = Object.keys(grid);
    const result = [];

    function backtrack(idx, current) {
        if (idx === keys.length) {
            result.push({ ...current });
            return;
        }
        const key = keys[idx];
        const values = grid[key];
        for (const val of values) {
            current[key] = val;
            backtrack(idx + 1, current);
        }
    }

    backtrack(0, {});
    return result;
}

module.exports = { searchParameters };
