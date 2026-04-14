// Optimization script for Turtle Breakout Strategy
const { searchParameters } = require('./parameterSearch');
const { createStrategy } = require('./strategies/turtleBreakout');

async function run() {
    const paramGrid = {
        leftBars: [1, 2, 3, 4, 5],
        rightBars: [1, 2, 3, 4, 5],
        minHoldBars: [0, 2, 4, 6, 8] // Testing some hold periods too
    };

    // 1. Optimize for BTC
    console.log('--- Optimizing for BTCUSDT ---');
    await searchParameters({
        createStrategy,
        symbol: 'BTCUSDT',
        paramGrid
    });

    // 2. Optimize for SOL
    console.log('\n--- Optimizing for SOLUSDT ---');
    await searchParameters({
        createStrategy,
        symbol: 'SOLUSDT',
        paramGrid
    });
}

run().catch(console.error);
