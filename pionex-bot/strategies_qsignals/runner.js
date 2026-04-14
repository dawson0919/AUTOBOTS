#!/usr/bin/env node
/**
 * Q-SIGNALS strategy runner bridge.
 *
 * Reads JSON from stdin:
 *   {
 *     "strategy": "dual_st_breakout",         // id matching strategies/*.js
 *     "symbol": "BTCUSDT",
 *     "timeframe": "1h",
 *     "params": { ... },                       // optional override
 *     "candles": [ {open,high,low,close,volume,time}, ... ]
 *   }
 *
 * Writes JSON to stdout:
 *   { "signal": "BUY" | "SELL" | "CLOSE_LONG" | "CLOSE_SHORT" | "HOLD",
 *     "price": <close at last candle>,
 *     "strategy": "<id>",
 *     "params_used": { ... } }
 *
 * Strategies are the Q-SIGNALS JS modules under qsignals_src/engine/strategies/.
 */
const path = require('path');
const indicators = require('./qsignals_src/engine/indicators.js');

const STRATEGY_DIR = path.join(__dirname, 'qsignals_src', 'engine', 'strategies');

// Map strategy id → module file
const REGISTRY = {
    dual_st_breakout: 'dualSuperTrend',
    donchian_trend: 'donchianTrend',
    dual_ema: 'dualEma',
    granville_eth_4h: 'granville_eth_4h',
    ichimoku_cloud: 'ichimoku_cloud',
    ma60: 'ma60',
    macd_ma: 'macdMa',
    mean_reversion: 'meanReversion',
    three_style: 'threeStyle',
    turtle_breakout: 'turtleBreakout',
};

function loadStrategy(id) {
    const file = REGISTRY[id];
    if (!file) throw new Error(`Unknown strategy id: ${id}`);
    return require(path.join(STRATEGY_DIR, `${file}.js`));
}

async function main() {
    let raw = '';
    for await (const chunk of process.stdin) raw += chunk;
    const input = JSON.parse(raw);

    const mod = loadStrategy(input.strategy);
    const candles = input.candles;
    if (!candles || candles.length < 50) {
        throw new Error(`Need at least 50 candles, got ${candles ? candles.length : 0}`);
    }

    // Build execute() with per-symbol optimized params if createStrategy exists
    const ctxParams = {
        symbol: input.symbol,
        timeframe: input.timeframe,
        ...(mod.defaultParams || {}),
        ...(input.params || {}),
    };
    const execute = typeof mod.createStrategy === 'function'
        ? mod.createStrategy(ctxParams)
        : mod.execute;

    // Pre-compute indicator pools (mirrors Q-SIGNALS backtester.js bootstrap)
    const closes = candles.map(c => c.close);
    const highs = candles.map(c => c.high);
    const lows = candles.map(c => c.low);
    const volumes = candles.map(c => c.volume);
    const indicatorData = {
        sma: {}, ema: {}, rsi: {},
        close: closes, open: candles.map(c => c.open),
        high: highs, low: lows, volume: volumes,
    };
    [5, 8, 10, 15, 16, 17, 20, 30, 35, 50, 60, 90, 100, 120, 130, 156, 178, 200, 203, 250].forEach(p => {
        indicatorData.sma[p] = indicators.sma(closes, p);
        indicatorData.ema[p] = indicators.ema(closes, p);
    });
    indicatorData.rsi[14] = indicators.rsi(closes, 14);
    indicatorData.atr = indicators.atr(highs, lows, closes, 14);
    indicatorData.volumeSma = indicators.sma(volumes, 20);
    indicatorData.adx = indicators.adx(highs, lows, closes, 14);
    indicatorData.dc_40 = indicators.donchian(highs, lows, 40);
    indicatorData.dc_30 = indicators.donchian(highs, lows, 30);
    indicatorData.getDonchian = (p) => {
        const k = `dc_${p}`;
        if (!indicatorData[k]) indicatorData[k] = indicators.donchian(highs, lows, p);
        return indicatorData[k];
    };

    // Run through recent candles so stateful indicators warm up
    let signal = null;
    const startIdx = Math.max(60, 0);
    for (let i = startIdx; i < candles.length; i++) {
        let res = execute(candles, indicatorData, i, indicators);
        if (res && typeof res === 'object') res = res.signal;  // unwrap {signal,sl,tp}
        if (res) signal = res;
    }

    const out = {
        signal: signal || 'HOLD',
        price: candles[candles.length - 1].close,
        strategy: input.strategy,
        params_used: ctxParams,
        candle_count: candles.length,
    };
    process.stdout.write(JSON.stringify(out));
}

main().catch(err => {
    process.stderr.write(err.stack || err.message);
    process.exit(1);
});
