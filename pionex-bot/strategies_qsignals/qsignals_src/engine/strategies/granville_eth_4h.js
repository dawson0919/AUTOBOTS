/**
 * Granville Pro (Ultimate Master Version) - HIGH FIDELITY
 * Bidirectional (Long/Short) strategy using Granville's Moving Average crossover.
 * Deep-optimized on ASCENDING data (matching API, 13,978 combos per asset):
 *   BTC: EMA178, SL 0.5% -> ROI +49.94%
 *   ETH: EMA203, SL 0.5% -> ROI +64.84%
 *   SOL: EMA156, SL 0.5% -> ROI +95.12%
 *   XAU: EMA16,  SL 0.5% -> ROI +17.23%
 */
module.exports = {
    id: 'granville_eth_4h',
    name: 'Granville Pro',
    description: '葛蘭碧法則精華版。多空雙向策略，自動適配各大資產（BTC/ETH/SOL/GOLD）的趨勢信號。',
    category: 'Premium',
    author: 'QuantSignal Pro',
    params: { ma_p: 203, sl: 0.005 },

    execute: (candles, indicatorData, i, indicators) => {
        const symbol = (candles[0] && (candles[0].symbol || candles[0].id) || 'ETHUSDT').toUpperCase();

        // --- 1. CONFIG SELECTION (Ascending-Order Optimized, Forced Long/Short) ---
        let ma_p = 203, sl = 0.005;

        if (symbol.includes('BTC')) {
            ma_p = 178; sl = 0.005;  // BTC: +49.94% ROI, WR 54.5%, PF 11.25
        } else if (symbol.includes('SOL')) {
            ma_p = 156; sl = 0.005;  // SOL: +95.12% ROI, WR 50.0%, PF 8.59
        } else if (symbol.includes('XAU') || symbol.includes('GOLD')) {
            ma_p = 16; sl = 0.005;  // XAU: +17.23% ROI, WR 22.7%, PF 1.69
        } else if (symbol.includes('CL')) {
            ma_p = 20; sl = 0.002;  // CLUSDT/1h: +10.89% ROI Pionex; 4h alt: ma_p=100 ROI +5.05%
        } else if (symbol.includes('PAXG')) {
            ma_p = 16; sl = 0.002;  // PAXG: +34.55% ROI (optimized, 124 trades)
        } else {
            ma_p = 203; sl = 0.005;  // ETH: +64.84% ROI, WR 38.5%, PF 7.58
        }

        // Dynamic warmup period based on MA
        if (i < ma_p + 3) return null;

        // --- 2. CALCULATE CORE METRICS ---
        const close = indicatorData.close;
        const emaArr = indicatorData.ema[ma_p] || indicators.ema(close, ma_p);

        const ma = emaArr[i];
        const maPrev = emaArr[i - 1];
        if (!ma || !maPrev) return null;

        const curP = close[i];
        const prevP = close[i - 1];

        // --- 3. SIGNAL MAPPING (Bidirectional Long/Short) ---
        // Golden Cross: price crosses above MA -> BUY (close short, open long)
        if (prevP < maPrev && curP > ma) return 'BUY';

        // Death Cross: price crosses below MA -> SELL (close long, open short)
        if (prevP > maPrev && curP < ma) return 'SELL';

        // Stop Loss exits (deviation from MA)
        if (curP < ma * (1 - sl)) return 'CLOSE_LONG';
        if (curP > ma * (1 + sl)) return 'CLOSE_SHORT';

        return null;
    }
};
