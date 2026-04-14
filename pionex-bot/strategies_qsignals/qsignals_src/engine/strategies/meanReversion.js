const pineScript = `
//@version=5
strategy("Mean Reversion Strategy", overlay=true)

bb_period = input(20, "BB Period")
bb_std = input(2.0, "BB StdDev")
rsi_period = input(14, "RSI Period")
rsi_oversold = input(30, "RSI Oversold")
rsi_overbought = input(70, "RSI Overbought")
vol_mult = input(1.5, "Volume Multiplier")
sl_atr_mult = input(2.0, "SL ATR Multiplier")

[upper, middle, lower] = ta.bb(close, bb_period, bb_std)
rsi_val = ta.rsi(close, rsi_period)
vol_sma = ta.sma(volume, 20)
atr_val = ta.atr(14)

vol_surge = volume > vol_sma * vol_mult

long_cond = close < lower and rsi_val < rsi_oversold and vol_surge
short_cond = close > upper and rsi_val > rsi_overbought and vol_surge

if (long_cond)
    strategy.entry("Long", strategy.long)
    strategy.exit("Exit Long", "Long", limit=middle, stop=close - atr_val * sl_atr_mult)

if (short_cond)
    strategy.entry("Short", strategy.short)
    strategy.exit("Exit Short", "Short", limit=middle, stop=close + atr_val * sl_atr_mult)

if (strategy.position_size > 0 and close >= middle)
    strategy.close("Long")
if (strategy.position_size < 0 and close <= middle)
    strategy.close("Short")
`;

function createStrategy(params = {}) {
    const BB_PERIOD = params.bb_period || 20;
    const BB_STD = params.bb_std || 2.0;
    const RSI_PERIOD = params.rsi_period || 14;
    const RSI_OVERSOLD = params.rsi_oversold || 30;
    const RSI_OVERBOUGHT = params.rsi_overbought || 70;
    const VOL_MULT = params.vol_mult || 1.5;
    const SL_ATR_MULT = params.sl_atr_mult || 2.0;

    // State tracking
    let position = null; // 'LONG', 'SHORT'
    let slPrice = 0;

    return function execute(candles, indicatorData, i, indicators) {
        // Compute indicators lazily / caching
        if (!indicatorData.bollinger) {
            indicatorData.bollinger = indicators.bollingerBands(indicatorData.close, BB_PERIOD, BB_STD);
            indicatorData.volSma = indicators.sma(indicatorData.volume, 20);
            indicatorData.atr = indicators.atr(indicatorData.high, indicatorData.low, indicatorData.close, 14);
        }

        // Wait for sufficient data
        if (i < Math.max(BB_PERIOD, RSI_PERIOD, 20)) return null;

        const close = indicatorData.close[i];
        const low = indicatorData.low[i];
        const high = indicatorData.high[i];
        const volume = indicatorData.volume[i];

        const bb = {
            upper: indicatorData.bollinger.upper[i],
            middle: indicatorData.bollinger.middle[i],
            lower: indicatorData.bollinger.lower[i],
        };
        const rsiVal = indicatorData.rsi[RSI_PERIOD][i]; // Assuming RSI 14 is pre-calc or we need to calc?
        // Check if RSI 14 exists in indicatorData (backtester provides rsi[14])
        // If not, we might crash. Backtester Step 3140 says: indicatorData.rsi[14] = indicators.rsi(closes, 14);
        // Correct.

        const volSmaVal = indicatorData.volSma[i];
        const atrVal = indicatorData.atr[i];

        if (bb.upper === null || rsiVal === null || volSmaVal === null || atrVal === null) return null;

        const volSurge = volume > volSmaVal * VOL_MULT;

        // Exit Logic (Priority)
        if (position === 'LONG') {
            // Check Stop Loss
            if (low <= slPrice) {
                position = null;
                return 'CLOSE_LONG';
            }
            // Check Take Profit (Mid Band)
            if (close >= bb.middle) {
                position = null;
                return 'CLOSE_LONG';
            }
        } else if (position === 'SHORT') {
            // Check Stop Loss
            if (high >= slPrice) {
                position = null;
                return 'CLOSE_SHORT';
            }
            // Check Take Profit (Mid Band)
            if (close <= bb.middle) {
                position = null;
                return 'CLOSE_SHORT';
            }
        }

        // Entry Logic
        const longCond = close < bb.lower && rsiVal < RSI_OVERSOLD && volSurge;
        const shortCond = close > bb.upper && rsiVal > RSI_OVERBOUGHT && volSurge;

        if (longCond) {
            // Close Short if any (Backtester handles flip, but we handle state)
            if (position === 'SHORT') {
                position = 'LONG';
                slPrice = close - (atrVal * SL_ATR_MULT);
                return 'BUY'; // Represents flip
            } else if (!position) {
                position = 'LONG';
                slPrice = close - (atrVal * SL_ATR_MULT);
                return 'BUY';
            }
        }

        if (shortCond) {
            if (position === 'LONG') {
                position = 'SHORT';
                slPrice = close + (atrVal * SL_ATR_MULT);
                return 'SELL'; // Flip
            } else if (!position) {
                position = 'SHORT';
                slPrice = close + (atrVal * SL_ATR_MULT);
                return 'SELL';
            }
        }

        return null;
    };
}

module.exports = {
    id: 'mean_reversion',
    name: 'Mean Reversion (BB + RSI)',
    description: '均值回歸策略：適合 ETH / SOL。利用布林通道與 RSI 超買超賣進行反向操作。',
    category: 'Premium',
    author: 'QuantSignal',
    pineScript,
    createStrategy,
    execute: createStrategy(),
    defaultParams: {
        bb_period: 20,
        bb_std: 2.0,
        rsi_period: 14,
        rsi_oversold: 30,
        rsi_overbought: 70,
        vol_mult: 1.5,
        sl_atr_mult: 2.0
    }
};
