// Premium Strategy: 唐奇安通道動量策略 (Donchian Momentum Strategy)
// 最佳化參數 (4H)：Period=40, ADX>20, SL=2.5x ATR, TP=8.0x ATR
// 最佳化參數 (1H)：Period=30, ADX>20, SL=2.0x ATR, TP=8.0x ATR

const pineScript = `//@version=6
strategy("Donchian Momentum Strategy", overlay=true, default_qty_type=strategy.percent_of_equity, default_qty_value=100)

dc_period  = input.int(40,   "唐奇安通道週期")
adx_thresh = input.int(20,   "ADX 門檻")
sl_mult    = input.float(2.5, "停損倍數 (ATR)")
tp_mult    = input.float(8.0, "停利倍數 (ATR)")
vol_filter = input.float(1.1, "成交量過濾倍數")
vol_period = input.int(20,    "成交量均線週期")

dc_upper = ta.highest(high, dc_period)
dc_lower = ta.lowest(low, dc_period)
[di_plus, di_minus, adx_val] = ta.dmi(14, 14)
atr_val = ta.atr(14)
vol_sma = ta.sma(volume, vol_period)
vol_ok  = volume > vol_sma * vol_filter
trend_ok = adx_val > adx_thresh

long_signal  = close > dc_upper[1] and trend_ok and vol_ok
short_signal = close < dc_lower[1] and trend_ok and vol_ok

if (long_signal)
    strategy.entry("Long", strategy.long)
if (short_signal)
    strategy.entry("Short", strategy.short)`;

function createStrategy(params = {}) {
    let DC_PERIOD = params.donchianPeriod || 40;
    let ADX_THRESHOLD = params.adxThreshold || 20;
    let SL_MULT = params.slMult || 2.5;
    let TP_MULT = params.tpMult || 8.0;
    let VOL_FILTER = params.volFilter || 1.1;
    let VOL_PERIOD = params.volPeriod || 20;

    // Apply Optimized Parameters based on timeframe
    if (params.timeframe === '1h') {
        DC_PERIOD = 30;
        SL_MULT = 2.0;
    }

    // Index/Futures — optimized per-symbol via param sweep
    if (params.symbol === 'NQUSDT') {
        DC_PERIOD = 10; ADX_THRESHOLD = 25; SL_MULT = 1.5; TP_MULT = 4.0;
        console.log(`[DonchianTrend] Init NQUSDT Optimized: P=${DC_PERIOD} ADX>${ADX_THRESHOLD} SL=${SL_MULT}x TP=${TP_MULT}x`);
    }
    if (params.symbol === 'SPXUSDT') {
        DC_PERIOD = 25; ADX_THRESHOLD = 25; SL_MULT = 1.0; TP_MULT = 3.0;
        console.log(`[DonchianTrend] Init SPXUSDT Optimized: P=${DC_PERIOD} ADX>${ADX_THRESHOLD} SL=${SL_MULT}x TP=${TP_MULT}x`);
    }
    if (params.symbol === 'ESUSDT') {
        DC_PERIOD = 15; ADX_THRESHOLD = 30; SL_MULT = 1.0; TP_MULT = 4.0;
        console.log(`[DonchianTrend] Init ESUSDT Optimized: P=${DC_PERIOD} ADX>${ADX_THRESHOLD} SL=${SL_MULT}x TP=${TP_MULT}x`);
    }
    if (params.symbol === 'SOLUSDT' && params.timeframe === '4h') {
        DC_PERIOD = 70; ADX_THRESHOLD = 20; SL_MULT = 2.0; TP_MULT = 6.0; VOL_FILTER = 1.0;
        console.log(`[DonchianTrend] Init SOLUSDT/4h Optimized: P=${DC_PERIOD} ADX>${ADX_THRESHOLD} SL=${SL_MULT}x TP=${TP_MULT}x`);
    }
    if (params.symbol === 'CLUSDT' && params.timeframe === '1h') {
        DC_PERIOD = 10; ADX_THRESHOLD = 20; SL_MULT = 1.0; TP_MULT = 6.0;
        console.log(`[DonchianTrend] Init CLUSDT/1h Optimized: P=${DC_PERIOD} ADX>${ADX_THRESHOLD} SL=${SL_MULT}x TP=${TP_MULT}x`);
    }

    return function execute(candles, indicatorData, i, indicators) {
        // Minimum bars for indicators
        if (i < Math.max(DC_PERIOD, VOL_PERIOD, 30)) return null;

        const close = indicatorData.close[i];
        const volume = indicatorData.volume[i];

        // ADX Trend Filter
        const adx = indicatorData.adx[i];
        const trendOk = adx !== null && adx > ADX_THRESHOLD;

        // Volume Filter - Handle cases with no volume data (e.g. some Gold feeds)
        const volSma = indicatorData.volumeSma[i];
        const volOk = (!volSma || volSma === 0) ? true : (volume > volSma * VOL_FILTER);
        if (!volOk) return null;

        // Donchian Breakout (using previous bar's channel)
        const dc = indicatorData.getDonchian(DC_PERIOD);
        const dcPrevUpper = dc.upper[i - 1];
        const dcPrevLower = dc.lower[i - 1];

        if (!trendOk || dcPrevUpper === null) return null;

        const atrVal = indicatorData.atr[i];

        // BUY: Close crosses above previous High
        if (close > dcPrevUpper) {
            return {
                signal: 'BUY',
                sl: close - atrVal * SL_MULT,
                tp: close + atrVal * TP_MULT
            };
        }

        // SELL: Close crosses below previous Low
        if (close < dcPrevLower) {
            return {
                signal: 'SELL',
                sl: close + atrVal * SL_MULT,
                tp: close - atrVal * TP_MULT
            };
        }

        return null;
    };
}

module.exports = {
    id: 'donchian_trend',
    name: '唐奇安動量策略',
    description: '唐奇安通道動量策略：結合通道突破、ADX 趨勢強度與成交量過濾器。在 1H 與 4H 時框表現極其穩定，是趨勢追蹤的首選。',
    category: 'Premium',
    author: 'QuantSignal',
    adminNotes: '[Optimization Report]\n4H: Period=40, ADX>20, SL=2.5x, TP=8.0x\n1H: Period=30, ADX>20, SL=2.0x, TP=8.0x\nNQUSDT: P=10, ADX>25, SL=1.5x, TP=4.0x\nSPXUSDT: P=25, ADX>25, SL=1.0x, TP=3.0x\nESUSDT: P=15, ADX>30, SL=1.0x, TP=4.0x',
    pineScript,
    createStrategy,
    execute: createStrategy({}), // Default (4H optimized)
    defaultParams: { donchianPeriod: 40, adxThreshold: 20, slMult: 2.5, tpMult: 8.0, volFilter: 1.1, volPeriod: 20 }
};
