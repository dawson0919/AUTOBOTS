// Premium Strategy: 海龜交易策略 (Turtle Breakout Strategy)
// Uses pivot highs/lows to detect breakout entries
// BUY on breakout above pivot high, SELL (short) on breakdown below pivot low

const pineScript = `//@version=5
strategy("海龜交易策略", overlay=true)
leftBars = input(4)
rightBars = input(2)
swh = ta.pivothigh(leftBars, rightBars)
swl = ta.pivotlow(leftBars, rightBars)
swh_cond = not na(swh)
hprice = 0.0
hprice := swh_cond ? swh : hprice[1]
le = false
le := swh_cond ? true : (le[1] and high > hprice ? false : le[1])
if (le)
    strategy.entry("PivRevLE", strategy.long, comment="突破多", stop=hprice + syminfo.mintick)
swl_cond = not na(swl)
lprice = 0.0
lprice := swl_cond ? swl : lprice[1]
se = false
se := swl_cond ? true : (se[1] and low < lprice ? false : se[1])
if (se)
    strategy.entry("PivRevSE", strategy.short, comment="跌破空", stop=lprice - syminfo.mintick)`;

function createStrategy(params = {}) {
    let LEFT = params.leftBars || 4;
    let RIGHT = params.rightBars || 2;
    let MIN_HOLD_BARS = params.minHoldBars || 6;

    // Apply Optimized Parameters for Gold
    if (params.symbol === 'XAUUSDT') {
        if (params.timeframe === '1h') {
            LEFT = 8; RIGHT = 2; MIN_HOLD_BARS = 2;
        } else if (params.timeframe === '4h') {
            LEFT = 15; RIGHT = 2; MIN_HOLD_BARS = 4;
        }
    }

    // Apply Optimized Parameters for SPX (S&P 500 / SPY)
    if (params.symbol === 'SPXUSDT') {
        if (params.timeframe === '4h') {
            LEFT = 6; RIGHT = 5; MIN_HOLD_BARS = 15;
        } else if (params.timeframe === '1h') {
            LEFT = 20; RIGHT = 2; MIN_HOLD_BARS = 40;
        }
    }

    // Apply Optimized Parameters for NAS (Nasdaq 100 / QQQ) and NQ (Futures)
    if (params.symbol === 'NASUSDT' || params.symbol === 'NQUSDT') {
        if (params.timeframe === '4h') {
            LEFT = 12; RIGHT = 4; MIN_HOLD_BARS = 2;
        }
    }

    // Apply Optimized Parameters for SOL 4h
    if (params.symbol === 'SOLUSDT' && params.timeframe === '4h') {
        LEFT = 12; RIGHT = 5; MIN_HOLD_BARS = 15;
    }

    // Apply Optimized Parameters for CLUSDT (WTI Crude Oil) 1h — Pionex 21d/500bars
    if (params.symbol === 'CLUSDT' && params.timeframe === '1h') {
        LEFT = 4; RIGHT = 3; MIN_HOLD_BARS = 2;
        console.log(`[TurtleBreakout] Init CLUSDT/1h Optimized: L=${LEFT} R=${RIGHT} Hold=${MIN_HOLD_BARS}`);
    }

    // Persistent state inside closure
    let hprice = 0;
    let lprice = 0;
    let le = false;
    let se = false;
    let lastSignalBar = -999;

    function pivotHigh(highs, idx) {
        const p = idx - RIGHT;
        if (p < LEFT) return null;
        const val = highs[p];
        for (let j = 1; j <= LEFT; j++)  if (highs[p - j] >= val) return null;
        for (let j = 1; j <= RIGHT; j++) if (highs[p + j] >= val) return null;
        return val;
    }

    function pivotLow(lows, idx) {
        const p = idx - RIGHT;
        if (p < LEFT) return null;
        const val = lows[p];
        for (let j = 1; j <= LEFT; j++)  if (lows[p - j] <= val) return null;
        for (let j = 1; j <= RIGHT; j++) if (lows[p + j] <= val) return null;
        return val;
    }

    return function execute(candles, indicatorData, i, indicators) {
        if (i <= LEFT + RIGHT) {
            hprice = 0; lprice = 0; le = false; se = false; lastSignalBar = -999;
            return null;
        }

        const high = indicatorData.high[i];
        const low = indicatorData.low[i];

        // Detect confirmed pivots
        const swh = pivotHigh(indicatorData.high, i);
        const swl = pivotLow(indicatorData.low, i);

        // Update pivot-high tracking
        if (swh !== null) { hprice = swh; le = true; }

        // Update pivot-low tracking
        if (swl !== null) { lprice = swl; se = true; }

        // Enforce minimum hold period to prevent whipsaws
        const barsSinceSignal = i - lastSignalBar;
        if (barsSinceSignal < MIN_HOLD_BARS) return null;

        // BUY: breakout above pivot high
        if (le && hprice > 0 && high > hprice) {
            le = false;
            lastSignalBar = i;
            return 'BUY';
        }

        // SELL: breakdown below pivot low
        if (se && lprice > 0 && low < lprice) {
            se = false;
            lastSignalBar = i;
            return 'SELL';
        }

        return null;
    };
}

const LEFT = 2;
const RIGHT = 5;
const MIN_HOLD_BARS = 2;

// ... (rest of the functions stay the same)

module.exports = {
    id: 'turtle_breakout',
    name: '海龜交易策略',
    description: '海龜突破策略：利用 Pivot High/Low 判斷市場結構，具有極佳的趨勢捕捉能力。已針對黃金 (15/2/4)、標普 500 (6/5/15) 及主流加密貨幣完成參數優化調校。',
    category: 'Premium',
    author: 'QuantSignal',
    adminNotes: '[Optimization Report]\n\n[XAU / GOLD]\n4H (180d): L15, R2, Hold4 -> Return +15.51%\n1H (45d): L8, R2, Hold2 -> Return +1.57%\n\n[SPX / SPY]\n4H (90d): L6, R5, Hold15 -> Return +2.12%\n1H (90d): L20, R2, Hold40 -> Return +1.38%\n\n[NAS / QQQ / NQ]\n4H (90d): L12, R4, Hold2 -> Return +6.08%, WinRate: 63.64%, PF: 3.76\n1H (90d): Recommendation 4H only (Noise)',
    pineScript,
    createStrategy,
    execute: createStrategy({ leftBars: LEFT, rightBars: RIGHT, minHoldBars: MIN_HOLD_BARS }), // Optimized default
    defaultParams: { leftBars: LEFT, rightBars: RIGHT, minHoldBars: MIN_HOLD_BARS }
};
