// Technical Indicators Library

/**
 * Simple Moving Average
 */
function sma(data, period) {
    const result = new Array(data.length).fill(null);
    for (let i = period - 1; i < data.length; i++) {
        let sum = 0;
        for (let j = i - period + 1; j <= i; j++) {
            sum += data[j];
        }
        result[i] = sum / period;
    }
    return result;
}

/**
 * Exponential Moving Average
 */
function ema(data, period) {
    const result = new Array(data.length).fill(null);
    const multiplier = 2 / (period + 1);

    // First EMA = SMA of first 'period' values
    let sum = 0;
    for (let i = 0; i < period; i++) {
        sum += data[i];
    }
    result[period - 1] = sum / period;

    for (let i = period; i < data.length; i++) {
        result[i] = (data[i] - result[i - 1]) * multiplier + result[i - 1];
    }
    return result;
}

/**
 * RSI - Relative Strength Index
 */
function rsi(data, period = 14) {
    const result = new Array(data.length).fill(null);
    const gains = [];
    const losses = [];

    for (let i = 1; i < data.length; i++) {
        const diff = data[i] - data[i - 1];
        gains.push(diff > 0 ? diff : 0);
        losses.push(diff < 0 ? Math.abs(diff) : 0);
    }

    // First RSI uses SMA
    let avgGain = 0, avgLoss = 0;
    for (let i = 0; i < period; i++) {
        avgGain += gains[i];
        avgLoss += losses[i];
    }
    avgGain /= period;
    avgLoss /= period;

    if (avgLoss === 0) {
        result[period] = 100;
    } else {
        result[period] = 100 - (100 / (1 + avgGain / avgLoss));
    }

    // Subsequent RSI uses Wilder's smoothing
    for (let i = period; i < gains.length; i++) {
        avgGain = (avgGain * (period - 1) + gains[i]) / period;
        avgLoss = (avgLoss * (period - 1) + losses[i]) / period;
        if (avgLoss === 0) {
            result[i + 1] = 100;
        } else {
            result[i + 1] = 100 - (100 / (1 + avgGain / avgLoss));
        }
    }
    return result;
}

/**
 * MACD
 */
function macd(data, fastPeriod = 12, slowPeriod = 26, signalPeriod = 9) {
    const fastEma = ema(data, fastPeriod);
    const slowEma = ema(data, slowPeriod);

    const macdLine = new Array(data.length).fill(null);
    for (let i = 0; i < data.length; i++) {
        if (fastEma[i] !== null && slowEma[i] !== null) {
            macdLine[i] = fastEma[i] - slowEma[i];
        }
    }

    // Signal line = EMA of MACD line
    const validMacd = macdLine.filter(v => v !== null);
    const signalLine = ema(validMacd, signalPeriod);

    // Align signal line
    const result = {
        macd: macdLine,
        signal: new Array(data.length).fill(null),
        histogram: new Array(data.length).fill(null)
    };

    let signalIdx = 0;
    for (let i = 0; i < data.length; i++) {
        if (macdLine[i] !== null) {
            if (signalIdx < signalLine.length && signalLine[signalIdx] !== null) {
                result.signal[i] = signalLine[signalIdx];
                result.histogram[i] = macdLine[i] - signalLine[signalIdx];
            }
            signalIdx++;
        }
    }

    return result;
}

/**
 * Crossover - returns true when a crosses above b at index i
 */
function crossover(a, b, i) {
    if (i < 1) return false;
    if (a[i] === null || a[i - 1] === null || b[i] === null || b[i - 1] === null) return false;
    return a[i - 1] <= b[i - 1] && a[i] > b[i];
}

/**
 * Crossunder - returns true when a crosses below b at index i
 */
function crossunder(a, b, i) {
    if (i < 1) return false;
    if (a[i] === null || a[i - 1] === null || b[i] === null || b[i - 1] === null) return false;
    return a[i - 1] >= b[i - 1] && a[i] < b[i];
}

/**
 * Average Directional Index (ADX)
 */
function adx(high, low, close, period = 14) {
    const n = high.length;
    const tr = new Array(n).fill(0);
    const plusDM = new Array(n).fill(0);
    const minusDM = new Array(n).fill(0);

    for (let i = 1; i < n; i++) {
        const hl = high[i] - low[i];
        const hc = Math.abs(high[i] - close[i - 1]);
        const lc = Math.abs(low[i] - close[i - 1]);
        tr[i] = Math.max(hl, hc, lc);

        const upMove = high[i] - high[i - 1];
        const downMove = low[i - 1] - low[i];

        if (upMove > downMove && upMove > 0) {
            plusDM[i] = upMove;
        } else {
            plusDM[i] = 0;
        }

        if (downMove > upMove && downMove > 0) {
            minusDM[i] = downMove;
        } else {
            minusDM[i] = 0;
        }
    }

    const smoothTR = new Array(n).fill(0);
    const smoothPlusDM = new Array(n).fill(0);
    const smoothMinusDM = new Array(n).fill(0);

    let trSum = 0;
    let plusSum = 0;
    let minusSum = 0;

    for (let i = 0; i < period; i++) {
        trSum += tr[i];
        plusSum += plusDM[i];
        minusSum += minusDM[i];
    }

    smoothTR[period - 1] = trSum;
    smoothPlusDM[period - 1] = plusSum;
    smoothMinusDM[period - 1] = minusSum;

    for (let i = period; i < n; i++) {
        smoothTR[i] = smoothTR[i - 1] - (smoothTR[i - 1] / period) + tr[i];
        smoothPlusDM[i] = smoothPlusDM[i - 1] - (smoothPlusDM[i - 1] / period) + plusDM[i];
        smoothMinusDM[i] = smoothMinusDM[i - 1] - (smoothMinusDM[i - 1] / period) + minusDM[i];
    }

    const plusDI = new Array(n).fill(0);
    const minusDI = new Array(n).fill(0);
    const dx = new Array(n).fill(0);

    for (let i = period - 1; i < n; i++) {
        plusDI[i] = 100 * (smoothPlusDM[i] / smoothTR[i]);
        minusDI[i] = 100 * (smoothMinusDM[i] / smoothTR[i]);
        const sumDI = plusDI[i] + minusDI[i];
        dx[i] = sumDI === 0 ? 0 : 100 * Math.abs(plusDI[i] - minusDI[i]) / sumDI;
    }

    const adxResult = new Array(n).fill(null);
    let dxSum = 0;
    for (let i = period - 1; i < period * 2 - 1; i++) {
        dxSum += dx[i];
    }
    adxResult[period * 2 - 2] = dxSum / period;

    for (let i = period * 2 - 1; i < n; i++) {
        adxResult[i] = (adxResult[i - 1] * (period - 1) + dx[i]) / period;
    }

    return adxResult;
}

/**
 * Ichimoku Cloud Components
 */
function ichimoku(high, low, close, p1 = 9, p2 = 26, p3 = 52) {
    const n = close.length;
    const tenkan = new Array(n).fill(null);
    const kijun = new Array(n).fill(null);
    const spanA = new Array(n).fill(null);
    const spanB = new Array(n).fill(null);
    const chikou = new Array(n).fill(null);

    for (let i = 0; i < n; i++) {
        // Tenkan-Sen: (9-period high + 9-period low) / 2
        if (i >= p1 - 1) {
            let hh = -Infinity, ll = Infinity;
            for (let j = 0; j < p1; j++) {
                if (high[i - j] > hh) hh = high[i - j];
                if (low[i - j] < ll) ll = low[i - j];
            }
            tenkan[i] = (hh + ll) / 2;
        }

        // Kijun-Sen: (26-period high + 26-period low) / 2
        if (i >= p2 - 1) {
            let hh = -Infinity, ll = Infinity;
            for (let j = 0; j < p2; j++) {
                if (high[i - j] > hh) hh = high[i - j];
                if (low[i - j] < ll) ll = low[i - j];
            }
            kijun[i] = (hh + ll) / 2;
        }

        // Senkou Span A: (Tenkan + Kijun) / 2, shifted forward 26 periods
        // We calculate 'now' and it will be used 'later'.
        // To keep alignment with current bar, Span A at index i is the value calculated at i-26.
        if (i >= p2) {
            const prevTenkan = tenkan[i - p2];
            const prevKijun = kijun[i - p2];
            if (prevTenkan !== null && prevKijun !== null) {
                spanA[i] = (prevTenkan + prevKijun) / 2;
            }
        }

        // Senkou Span B: (52-period high + 52-period low) / 2, shifted forward 26 periods
        if (i >= p3 + p2 - 1) {
            let hh = -Infinity, ll = Infinity;
            const startIdx = i - p2; // the calculation point
            for (let j = 0; j < p3; j++) {
                if (high[startIdx - j] > hh) hh = high[startIdx - j];
                if (low[startIdx - j] < ll) ll = low[startIdx - j];
            }
            spanB[i] = (hh + ll) / 2;
        }

        // Chikou Span: Close shifted back 26 periods
        // Chikou at index i is close at index i+26. 
        // For backtesting, we usually check if close[i] > high[i-26] or similar.
        // Actually, Chikou is just the close plotted 26 bars back.
        // So chikou[i-26] = close[i].
        // But for easier strategy logic: chikou[i] = close[i] vs price[i-26].
        if (i >= p2) {
            chikou[i] = close[i]; // The value is just close, but logically compared to candles[i-26]
        }
    }

    return { tenkan, kijun, spanA, spanB, chikou };
}

module.exports = {
    sma,
    ema,
    rsi,
    macd,
    crossover,
    crossunder,
    bollingerBands,
    atr,
    donchian,
    superTrend,
    adx,
    ichimoku
};

/**
 * Bollinger Bands
 */
function bollingerBands(data, period = 20, stdDev = 2) {
    const smaLine = sma(data, period);
    const upper = new Array(data.length).fill(null);
    const lower = new Array(data.length).fill(null);

    for (let i = period - 1; i < data.length; i++) {
        let sumSq = 0;
        for (let j = 0; j < period; j++) {
            sumSq += Math.pow(data[i - j] - smaLine[i], 2);
        }
        const std = Math.sqrt(sumSq / period);
        upper[i] = smaLine[i] + std * stdDev;
        lower[i] = smaLine[i] - std * stdDev;
    }

    return { upper, middle: smaLine, lower };
}

/**
 * Average True Range (ATR)
 */
function atr(high, low, close, period = 14) {
    const tr = new Array(high.length).fill(0);
    const result = new Array(high.length).fill(null);

    // Calculate TR
    for (let i = 0; i < high.length; i++) {
        if (i === 0) {
            tr[i] = high[i] - low[i];
        } else {
            const hl = high[i] - low[i];
            const hc = Math.abs(high[i] - close[i - 1]);
            const lc = Math.abs(low[i] - close[i - 1]);
            tr[i] = Math.max(hl, hc, lc);
        }
    }

    // First ATR is SMA of TR
    let sum = 0;
    for (let i = 0; i < period; i++) {
        sum += tr[i];
    }
    result[period - 1] = sum / period;

    // Subsequent ATR: (Previous ATR * (n-1) + Current TR) / n
    for (let i = period; i < high.length; i++) {
        result[i] = (result[i - 1] * (period - 1) + tr[i]) / period;
    }

    return result;
}

/**
 * Donchian Channel
 */
function donchian(high, low, period = 20) {
    const upper = new Array(high.length).fill(null);
    const lower = new Array(low.length).fill(null);
    const middle = new Array(high.length).fill(null);

    for (let i = period - 1; i < high.length; i++) {
        let max = -Infinity;
        let min = Infinity;
        for (let j = 0; j < period; j++) {
            if (high[i - j] > max) max = high[i - j];
            if (low[i - j] < min) min = low[i - j];
        }
        upper[i] = max;
        lower[i] = min;
        middle[i] = (max + min) / 2;
    }
    return { upper, lower, middle };
}

/**
 * SuperTrend
 */
function superTrend(high, low, close, period = 10, multiplier = 3) {
    const atrVal = atr(high, low, close, period);
    const superTrendPath = new Array(high.length).fill(null);
    const directions = new Array(high.length).fill(1); // 1: Up, -1: Down

    let finalUpperBand = 0;
    let finalLowerBand = 0;

    for (let i = period; i < high.length; i++) {
        if (atrVal[i] === null) continue;

        const basicUpperBand = (high[i] + low[i]) / 2 + multiplier * atrVal[i];
        const basicLowerBand = (high[i] + low[i]) / 2 - multiplier * atrVal[i];

        if (i === period) {
            finalUpperBand = basicUpperBand;
            finalLowerBand = basicLowerBand;
        } else {
            if (basicUpperBand < finalUpperBand || close[i - 1] > finalUpperBand) {
                finalUpperBand = basicUpperBand;
            } else {
                // Keep previous (do nothing, effectively finalUpperBand stays same)
                // Actually logic is: if not (basic < final or close > final) -> keep previous
                // But wait, the standard ST logic is:
                // IF (BusinessUpper < FinalUpper[1]) OR (Close[1] > FinalUpper[1]) THEN BasicUpper ELSE FinalUpper[1]
                // My logic:
                // if (basic < final) update
                // if (close[1] > final) update
                // else keep final. 
                // Matches.
            }

            if (basicLowerBand > finalLowerBand || close[i - 1] < finalLowerBand) {
                finalLowerBand = basicLowerBand;
            } else {
                // Keep previous
            }
        }

        let direction = (i === period) ? 1 : directions[i - 1];

        if (direction === 1) {
            if (close[i] < finalLowerBand) {
                direction = -1;
            }
        } else {
            if (close[i] > finalUpperBand) {
                direction = 1;
            }
        }

        directions[i] = direction;
        superTrendPath[i] = direction === 1 ? finalLowerBand : finalUpperBand;
    }

    return { value: superTrendPath, direction: directions };
}
