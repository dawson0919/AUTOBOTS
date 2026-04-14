// Core Backtesting Engine — supports LONG & SHORT positions
const indicators = require('./indicators');

class Backtester {
    constructor(options = {}) {
        this.initialCapital = options.initialCapital || 10000;
        this.positionSize = options.positionSize || 1; // fraction of capital per trade
        this.commission = options.commission !== undefined ? options.commission : 0; // Set to 0 by default
        this.slippage = options.slippage !== undefined ? options.slippage : 0; // Set to 0 by default
    }

    /**
     * Run a backtest with the given strategy function and candle data.
     *
     * Signals the strategy may return:
     *   'BUY'         – close any SHORT, then open LONG
     *   'SELL'        – close any LONG, then open SHORT
     *   'CLOSE_LONG'  – close LONG, go flat
     *   'CLOSE_SHORT' – close SHORT, go flat
     *   null          – do nothing
     */
    run(strategyFn, candles) {
        if (!candles || candles.length < 2) {
            return { error: 'Insufficient data for backtest' };
        }

        let capital = this.initialCapital;
        let position = null; // { type: 'LONG'|'SHORT', entryPrice, entryTime, size }
        const trades = [];
        const equityCurve = [];
        const closes = candles.map(c => c.close);

        // Pre-compute indicators
        const indicatorData = {
            sma: {}, ema: {}, rsi: {},
            close: closes,
            open: candles.map(c => c.open),
            high: candles.map(c => c.high),
            low: candles.map(c => c.low),
            volume: candles.map(c => c.volume),
        };

        [5, 8, 10, 15, 16, 17, 20, 30, 35, 50, 60, 90, 100, 120, 130, 156, 178, 200, 203, 250].forEach(p => {
            indicatorData.sma[p] = indicators.sma(closes, p);
            indicatorData.ema[p] = indicators.ema(closes, p);
        });
        indicatorData.rsi[14] = indicators.rsi(closes, 14);
        indicatorData.atr = indicators.atr(indicatorData.high, indicatorData.low, indicatorData.close, 14);
        indicatorData.volumeSma = indicators.sma(indicatorData.volume, 20);

        // Pre-compute ADX and Donchian for new strategies
        indicatorData.adx = indicators.adx(indicatorData.high, indicatorData.low, indicatorData.close, 14);
        indicatorData.dc_40 = indicators.donchian(indicatorData.high, indicatorData.low, 40);
        indicatorData.dc_30 = indicators.donchian(indicatorData.high, indicatorData.low, 30);

        // Helper to get specific Donchian
        indicatorData.getDonchian = (p) => {
            const key = `dc_${p}`;
            if (!indicatorData[key]) {
                indicatorData[key] = indicators.donchian(indicatorData.high, indicatorData.low, p);
            }
            return indicatorData[key];
        };

        // ── Helper for dynamic MACD ──────
        const getDynamicMacd = (f, s, sig) => {
            const key = `macd_${f}_${s}_${sig}`;
            if (!indicatorData[key]) {
                indicatorData[key] = indicators.macd(closes, f, s, sig);
            }
            return indicatorData[key];
        };
        indicatorData.getMacd = getDynamicMacd;

        // Pre-calculate common ones
        getDynamicMacd(8, 50, 7);
        getDynamicMacd(12, 26, 9);
        getDynamicMacd(5, 26, 7); // Gold Optimized 4h


        // ── Helper: close current position ──────────────────
        const closePosition = (exitPrice, exitTime) => {
            if (!position) return;

            if (position.type === 'LONG') {
                const slippedExit = exitPrice * (1 - this.slippage);
                const pnl = (slippedExit - position.entryPrice) * position.size;
                const commission = Math.abs(slippedExit * position.size) * this.commission;
                capital += pnl - commission;
                trades.push({
                    type: 'LONG',
                    entryPrice: position.entryPrice,
                    exitPrice: slippedExit,
                    entryTime: position.entryTime,
                    exitTime,
                    pnl,
                    pnlPercent: ((slippedExit - position.entryPrice) / position.entryPrice) * 100,
                    commission
                });
            } else {
                // SHORT: profit when price drops
                const slippedExit = exitPrice * (1 + this.slippage);
                const pnl = (position.entryPrice - slippedExit) * position.size;
                const commission = Math.abs(slippedExit * position.size) * this.commission;
                capital += pnl - commission;
                trades.push({
                    type: 'SHORT',
                    entryPrice: position.entryPrice,
                    exitPrice: slippedExit,
                    entryTime: position.entryTime,
                    exitTime,
                    pnl,
                    pnlPercent: ((position.entryPrice - slippedExit) / position.entryPrice) * 100,
                    commission
                });
            }
            position = null;
        };

        // ── Helper: open a new position ─────────────────────
        const openPosition = (type, price, time, sl = null, tp = null) => {
            const slippedEntry = type === 'LONG'
                ? price * (1 + this.slippage)
                : price * (1 - this.slippage);
            const size = (capital * this.positionSize) / slippedEntry;
            const commission = capital * this.positionSize * this.commission;
            capital -= commission;
            position = { type, entryPrice: slippedEntry, entryTime: time, size, slPrice: sl, tpPrice: tp };
        };

        // ── Main loop ───────────────────────────────────────
        for (let i = 1; i < candles.length; i++) {
            const candle = candles[i];
            const time = candle.open_time || candle.openTime;

            // 1. Check stop loss/take profit first (intra-bar estimation)
            if (position) {
                if (position.type === 'LONG') {
                    if (position.slPrice && candle.low <= position.slPrice) {
                        closePosition(position.slPrice, time);
                    } else if (position.tpPrice && candle.high >= position.tpPrice) {
                        closePosition(position.tpPrice, time);
                    }
                } else {
                    if (position.slPrice && candle.high >= position.slPrice) {
                        closePosition(position.slPrice, time);
                    } else if (position.tpPrice && candle.low <= position.tpPrice) {
                        closePosition(position.tpPrice, time);
                    }
                }
            }

            // 2. Get signal from strategy if still flat or for reversal
            const result = strategyFn(candles, indicatorData, i, indicators);
            const signal = (result && typeof result === 'object') ? result.signal : result;
            const sl = (result && typeof result === 'object') ? result.sl : null;
            const tp = (result && typeof result === 'object') ? result.tp : null;

            if (signal === 'BUY') {
                // Close short if open, then go long
                if (position && position.type === 'SHORT') closePosition(candle.close, time);
                if (!position) openPosition('LONG', candle.close, time, sl, tp);
            } else if (signal === 'SELL') {
                // Close long if open, then go short
                if (position && position.type === 'LONG') closePosition(candle.close, time);
                if (!position) openPosition('SHORT', candle.close, time, sl, tp);
            } else if (signal === 'CLOSE_LONG' && position && position.type === 'LONG') {
                closePosition(candle.close, time);
            } else if (signal === 'CLOSE_SHORT' && position && position.type === 'SHORT') {
                closePosition(candle.close, time);
            }

            // Record equity (including unrealized P&L)
            let equity = capital;
            if (position) {
                if (position.type === 'LONG') {
                    equity += (candle.close - position.entryPrice) * position.size;
                } else {
                    equity += (position.entryPrice - candle.close) * position.size;
                }
            }
            equityCurve.push({ time, equity, price: candle.close });
        }

        // Close any remaining position at last price
        if (position) {
            const lastCandle = candles[candles.length - 1];
            closePosition(lastCandle.close, lastCandle.open_time || lastCandle.openTime);
            // Update the last equity point to reflect realized commission
            if (equityCurve.length > 0) {
                equityCurve[equityCurve.length - 1].equity = capital;
            }
        }

        return this.generateReport(trades, equityCurve, candles);
    }

    generateReport(trades, equityCurve, candles) {
        const totalTrades = trades.length;
        const winningTrades = trades.filter(t => t.pnl > 0);
        const losingTrades = trades.filter(t => t.pnl <= 0);
        const longTrades = trades.filter(t => t.type === 'LONG');
        const shortTrades = trades.filter(t => t.type === 'SHORT');

        // Win rate
        const winRate = totalTrades > 0 ? (winningTrades.length / totalTrades) * 100 : 0;

        // Total return
        const finalEquity = equityCurve.length > 0 ? equityCurve[equityCurve.length - 1].equity : this.initialCapital;
        const totalReturn = ((finalEquity - this.initialCapital) / this.initialCapital) * 100;

        // Max drawdown
        let peak = this.initialCapital;
        let maxDrawdown = 0;
        for (const point of equityCurve) {
            if (point.equity > peak) peak = point.equity;
            const dd = ((peak - point.equity) / peak) * 100;
            if (dd > maxDrawdown) maxDrawdown = dd;
        }

        // 盈虧比 (P/L Ratio) = avgWin / |avgLoss| — computed after avgWin/avgLoss below

        // Profit factor
        const grossProfit = winningTrades.reduce((sum, t) => sum + t.pnl, 0);
        const grossLoss = Math.abs(losingTrades.reduce((sum, t) => sum + t.pnl, 0));
        const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0;

        // Recovery factor
        const recoveryFactor = maxDrawdown > 0 ? totalReturn / maxDrawdown : totalReturn;

        // Expectancy
        const avgWin = winningTrades.length > 0 ? winningTrades.reduce((s, t) => s + t.pnlPercent, 0) / winningTrades.length : 0;
        const avgLoss = losingTrades.length > 0 ? losingTrades.reduce((s, t) => s + t.pnlPercent, 0) / losingTrades.length : 0;
        const expectancy = (winRate / 100 * avgWin) + ((1 - winRate / 100) * avgLoss);

        // Average hold time
        const holdTimes = trades.map(t => t.exitTime - t.entryTime);
        const avgHoldTime = holdTimes.length > 0 ? holdTimes.reduce((a, b) => a + b, 0) / holdTimes.length : 0;
        const avgHoldDays = avgHoldTime / (1000 * 60 * 60 * 24);

        // Recent trades (last 10)
        const recentTrades = trades.slice(-10).reverse().map(t => ({
            ...t,
            entryDate: new Date(t.entryTime).toISOString(),
            exitDate: new Date(t.exitTime).toISOString(),
            pnlPercent: parseFloat(t.pnlPercent.toFixed(2))
        }));

        // 5. Equity curve (sampled for chart - max 200 points)
        // Ensure starting point is the startTime calculated in dataFetcher/routes
        const equityCurveStartTime = candles[0]?.open_time || candles[0]?.openTime;
        const filteredEquity = equityCurve.filter(p => p.time >= equityCurveStartTime);

        const step = Math.max(1, Math.floor(filteredEquity.length / 200));
        const sampledEquity = filteredEquity.filter((_, i) => i % step === 0 || i === filteredEquity.length - 1);

        return {
            summary: {
                initialCapital: this.initialCapital,
                finalEquity: parseFloat(finalEquity.toFixed(2)),
                totalReturn: parseFloat(totalReturn.toFixed(2)),
                maxDrawdown: parseFloat(maxDrawdown.toFixed(2)),
                winRate: parseFloat(winRate.toFixed(2)),
                timeframe: '4H',
                totalTrades,
                winningTrades: winningTrades.length,
                losingTrades: losingTrades.length,
                longTrades: longTrades.length,
                shortTrades: shortTrades.length,
                profitFactor: parseFloat(profitFactor.toFixed(2)),
                recoveryFactor: parseFloat(recoveryFactor.toFixed(2)),
                expectancy: parseFloat(expectancy.toFixed(2)),
                avgHoldDays: parseFloat(avgHoldDays.toFixed(1)),
                avgWin: parseFloat(avgWin.toFixed(2)),
                avgLoss: parseFloat(avgLoss.toFixed(2)),
                plRatio: parseFloat((avgLoss !== 0 ? Math.abs(avgWin / avgLoss) : avgWin > 0 ? Infinity : 0).toFixed(2)),
                period: {
                    start: equityCurveStartTime,
                    end: candles[candles.length - 1]?.open_time || candles[candles.length - 1]?.openTime,
                    startDate: new Date(equityCurveStartTime).toISOString().split('T')[0],
                    endDate: new Date(candles[candles.length - 1]?.open_time || candles[candles.length - 1]?.openTime).toISOString().split('T')[0]
                }
            },
            recentTrades,
            equityCurve: sampledEquity
        };
    }
}

module.exports = Backtester;
