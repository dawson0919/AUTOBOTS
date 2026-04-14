// Strategy: 三刀流趨勢策略 (Three-Style MA - Triple EMA Trend)
// Logic:
// 1. Uses 3 EMAs: Fast (20), Medium (50), Slow (200)
// 2. Buy Condition: Fast > Medium > Slow (Full Bullish Alignment)
// 3. Sell Condition: Fast < Medium < Slow (Full Bearish Alignment)
// 4. Exit: When alignment breaks
// Optimized: PAXGUSDT adds ADX filter, ATR SL/TP, RSI filter

const pineScript = `
//@version=5
strategy("均線三刀流", overlay=true)

fast_len = input.int(20, "Fast EMA")
mid_len = input.int(50, "Medium EMA")
slow_len = input.int(200, "Slow EMA")

fast_ema = ta.ema(close, fast_len)
mid_ema = ta.ema(close, mid_len)
slow_ema = ta.ema(close, slow_len)

// Bullish Alignment: Fast > Mid > Slow
bullish = fast_ema > mid_ema and mid_ema > slow_ema
// Bearish Alignment: Fast < Mid < Slow
bearish = fast_ema < mid_ema and mid_ema < slow_ema

if (bullish)
    strategy.entry("Long", strategy.long)

if (bearish)
    strategy.entry("Short", strategy.short)

// Optional: Exit when alignment breaks
if (strategy.position_size > 0 and not bullish)
    strategy.close("Long")
if (strategy.position_size < 0 and not bearish)
    strategy.close("Short")
`.trim();

function createStrategy(params = {}) {
    let fast = params.fast || 20;
    let mid = params.mid || 50;
    let slow = params.slow || 200;

    // Filter configuration (0 = disabled)
    let adxThreshold = params.adxThreshold || 0;
    let slMultiplier = params.slMultiplier || 0;
    let tpMultiplier = params.tpMultiplier || 0;
    let rsiOverbought = params.rsiOverbought || 0;
    let rsiOversold = params.rsiOversold || 0;

    // Apply Optimized Parameters for Gold
    if (params.symbol === 'XAUUSDT') {
        if (params.timeframe === '1h') {
            fast = 8; mid = 15; slow = 30;
            console.log(`[ThreeStyle] Init XAUUSDT 1H Optimized: ${fast}/${mid}/${slow}`);
        } else if (params.timeframe === '4h') {
            fast = 20; mid = 60; slow = 120;
            console.log(`[ThreeStyle] Init XAUUSDT 4H Optimized: ${fast}/${mid}/${slow}`);
        }
    }

    // Apply Optimized Parameters for PAXG (Tokenized Gold)
    if (params.symbol === 'PAXGUSDT') {
        if (params.timeframe === '1h') {
            fast = 10; mid = 50; slow = 200;
            adxThreshold = 15;
            slMultiplier = 2.0;
            tpMultiplier = 8.0;
            console.log(`[ThreeStyle] Init PAXGUSDT 1H Optimized: ${fast}/${mid}/${slow} ADX>${adxThreshold} SL=${slMultiplier}x TP=${tpMultiplier}x`);
        } else if (params.timeframe === '4h') {
            fast = 10; mid = 35; slow = 90;
            adxThreshold = 18;
            slMultiplier = 2.5;
            tpMultiplier = 6.0;
            rsiOverbought = 75;
            rsiOversold = 25;
            console.log(`[ThreeStyle] Init PAXGUSDT 4H Optimized: ${fast}/${mid}/${slow} ADX>${adxThreshold} SL=${slMultiplier}x TP=${tpMultiplier}x`);
        }
    }

    // Apply Optimized Parameters for SOLUSDT
    if (params.symbol === 'SOLUSDT') {
        if (params.timeframe === '4h') {
            fast = 5; mid = 50; slow = 120;
            adxThreshold = 20;
            slMultiplier = 2.5;
            tpMultiplier = 8.0;
            console.log(`[ThreeStyle] Init SOLUSDT 4H Optimized: ${fast}/${mid}/${slow} ADX>${adxThreshold} SL=${slMultiplier}x TP=${tpMultiplier}x`);
        }
    }

    // Apply Optimized Parameters for ETHUSDT
    if (params.symbol === 'ETHUSDT') {
        if (params.timeframe === '4h') {
            fast = 10; mid = 50; slow = 90;
            adxThreshold = 20;
            slMultiplier = 2.0;
            tpMultiplier = 5.0;
            console.log(`[ThreeStyle] Init ETHUSDT 4H Optimized: ${fast}/${mid}/${slow} ADX>${adxThreshold} SL=${slMultiplier}x TP=${tpMultiplier}x`);
        }
    }

    // Apply Optimized Parameters for BTCUSDT
    if (params.symbol === 'BTCUSDT') {
        if (params.timeframe === '4h') {
            fast = 5; mid = 35; slow = 50;
            adxThreshold = 15;
            slMultiplier = 2.5;
            tpMultiplier = 5.0;
            console.log(`[ThreeStyle] Init BTCUSDT 4H Optimized: ${fast}/${mid}/${slow} ADX>${adxThreshold} SL=${slMultiplier}x TP=${tpMultiplier}x`);
        }
    }

    // Apply Optimized Parameters for CLUSDT (WTI Crude Oil) 1h — Pionex 21d/500bars
    if (params.symbol === 'CLUSDT' && params.timeframe === '1h') {
        fast = 5; mid = 20; slow = 200;
        adxThreshold = 15;
        console.log(`[ThreeStyle] Init CLUSDT 1H Optimized: ${fast}/${mid}/${slow} ADX>${adxThreshold}`);
    }

    return function execute(candles, indicatorData, i, indicators) {
        const emaFast = indicatorData.ema[fast];
        const emaMid = indicatorData.ema[mid];
        const emaSlow = indicatorData.ema[slow];

        if (!emaFast || !emaMid || !emaSlow) return null;
        if (emaFast[i] === null || emaMid[i] === null || emaSlow[i] === null) return null;

        const f = emaFast[i];
        const m = emaMid[i];
        const s = emaSlow[i];

        // ── 1. ADX Trend Filter ──
        if (adxThreshold > 0) {
            const adx = indicatorData.adx ? indicatorData.adx[i] : null;
            if (adx === null || adx < adxThreshold) {
                // Low-trend: only allow exits, no new entries
                if (f < m) return 'CLOSE_LONG';
                if (f > m) return 'CLOSE_SHORT';
                return null;
            }
        }

        // ── 2. RSI values (read once) ──
        let rsiVal = null;
        if (rsiOverbought > 0 || rsiOversold > 0) {
            rsiVal = indicatorData.rsi && indicatorData.rsi[14] ? indicatorData.rsi[14][i] : null;
        }

        // ── 3. Entry: Full Bullish Alignment ──
        if (f > m && m > s) {
            // Skip BUY if RSI overbought
            if (rsiOverbought > 0 && rsiVal !== null && rsiVal > rsiOverbought) {
                return null;
            }
            // Return with ATR-based SL/TP if configured
            if (slMultiplier > 0 || tpMultiplier > 0) {
                const atrVal = indicatorData.atr ? indicatorData.atr[i] : null;
                if (atrVal && atrVal > 0) {
                    const close = indicatorData.close[i];
                    return {
                        signal: 'BUY',
                        sl: slMultiplier > 0 ? close - atrVal * slMultiplier : null,
                        tp: tpMultiplier > 0 ? close + atrVal * tpMultiplier : null
                    };
                }
            }
            return 'BUY';
        }

        // ── 4. Entry: Full Bearish Alignment ──
        if (f < m && m < s) {
            // Skip SELL if RSI oversold
            if (rsiOversold > 0 && rsiVal !== null && rsiVal < rsiOversold) {
                return null;
            }
            if (slMultiplier > 0 || tpMultiplier > 0) {
                const atrVal = indicatorData.atr ? indicatorData.atr[i] : null;
                if (atrVal && atrVal > 0) {
                    const close = indicatorData.close[i];
                    return {
                        signal: 'SELL',
                        sl: slMultiplier > 0 ? close + atrVal * slMultiplier : null,
                        tp: tpMultiplier > 0 ? close - atrVal * tpMultiplier : null
                    };
                }
            }
            return 'SELL';
        }

        // ── 5. Exit: Alignment breaks ──
        if (f < m) return 'CLOSE_LONG';
        if (f > m) return 'CLOSE_SHORT';

        return null;
    };
}

module.exports = {
    id: 'three_style',
    name: '均線三刀流 (Triple MA)',
    description: '三刀流趨勢策略：利用三條不同週期的 EMA 形成的多空排列捕捉趨勢。已針對黃金 1H (8/15/30) 與 4H (20/60/120)、PAXG 1H (8/15/30) 與 4H (10/35/90) 以及主流幣完成優化。PAXG 版本含 ADX 趨勢過濾與 ATR 動態停損停利。',
    category: 'Premium',
    author: 'QuantSignal',
    adminNotes: '[Optimization Report]\n\n[XAUUSDT]\n1H: 8/15/30\n4H: 20/60/120\n\n[PAXGUSDT]\n1H: 8/15/30, ADX>20, SL=2.0x ATR, TP=5.0x ATR, RSI 25/75\n4H: 10/35/90, ADX>18, SL=2.5x ATR, TP=6.0x ATR, RSI 25/75',
    pineScript,
    createStrategy,
    execute: createStrategy({ fast: 20, mid: 50, slow: 200 }),
    defaultParams: {
        fast: 20,
        mid: 50,
        slow: 200,
        adxThreshold: 0,
        slMultiplier: 0,
        tpMultiplier: 0,
        rsiOverbought: 0,
        rsiOversold: 0
    }
};
