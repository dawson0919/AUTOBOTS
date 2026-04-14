const YahooFinance = require('yahoo-finance2').default;
const yahoo = new YahooFinance();
const { insertCandles, getLatestCandleTime, getCandleCount } = require('./database');

// Yahoo Finance Info: NQ=F (Nasdaq 100 Futures), ES=F (S&P 500 Futures) - 15m delay
const SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'PAXGUSDT', 'XAUUSDT', 'SPXUSDT', 'NASUSDT', 'NQUSDT', 'ESUSDT', 'CLUSDT'];
const CG_ID_MAP = {
    'SPXUSDT': 'spdr-s-p-500-etf-ondo-tokenized-etf',
    'NASUSDT': 'invesco-qqq-etf-ondo-tokenized-etf'
};
const YAHOO_SYMBOL_MAP = {
    'NQUSDT': 'NQ=F',
    'ESUSDT': 'ES=F'
};
// Pionex perpetual contracts (WTI crude oil)
const PIONEX_SYMBOL_MAP = {
    'CLUSDT': 'WTI_USDT_PERP'
};
const TIMEFRAMES = ['1h', '4h'];
const BACKFILL_DAYS = parseInt(process.env.BACKFILL_DAYS || '365');
const BINANCE_SPOT_API = 'https://api.binance.com/api/v3/klines';
const BINANCE_FUTURES_API = 'https://fapi.binance.com/fapi/v1/klines';

async function fetchKlines(symbol, interval, startTime, endTime, limit = 1000) {
    // Route to CoinGecko for SPX/NAS/XAU
    if (CG_ID_MAP[symbol]) {
        let days = 90;
        return fetchKlinesFromCG(symbol, interval, days);
    }

    // Route to Yahoo Finance for NQ/ES (Delayed 15m)
    if (YAHOO_SYMBOL_MAP[symbol]) {
        return fetchKlinesFromYahoo(symbol, interval, startTime, endTime);
    }

    // Route to Pionex Perpetual for CL (WTI crude oil)
    if (PIONEX_SYMBOL_MAP[symbol]) {
        return fetchKlinesFromPionex(symbol, interval, limit);
    }

    const isFutures = symbol === 'XAUUSDT';
    const baseUrl = isFutures ? BINANCE_FUTURES_API : BINANCE_SPOT_API;

    const params = new URLSearchParams({
        symbol,
        interval,
        limit: String(limit),
    });
    if (startTime) params.append('startTime', String(startTime));
    if (endTime) params.append('endTime', String(endTime));

    const url = `${baseUrl}?${params.toString()}`;

    try {
        const res = await fetch(url);
        if (!res.ok) {
            throw new Error(`Binance API error: ${res.status} ${res.statusText}`);
        }
        const data = await res.json();

        return data.map(k => ({
            openTime: k[0],
            open: parseFloat(k[1]),
            high: parseFloat(k[2]),
            low: parseFloat(k[3]),
            close: parseFloat(k[4]),
            volume: parseFloat(k[5]),
            closeTime: k[6],
        }));
    } catch (err) {
        console.error(`[Backfill] Failed to fetch ${symbol}:`, err.message);
        return [];
    }
}

async function fetchKlinesFromYahoo(symbol, interval, startTime, endTime) {
    const yahooSymbol = YAHOO_SYMBOL_MAP[symbol];
    if (!yahooSymbol) return [];

    console.log(`[Backfill] Fetching ${symbol} (${yahooSymbol}) from Yahoo Finance (15m delay)...`);

    try {
        // Yahoo Finance supports 1h, but not 4h. Fetch 1h and aggregate.
        const yahooInterval = '1h';
        const queryOptions = {
            period1: Math.floor(startTime / 1000),
            period2: Math.floor(endTime / 1000),
            interval: yahooInterval
        };

        const result = await yahoo.chart(yahooSymbol, queryOptions);
        if (!result || !result.quotes || result.quotes.length === 0) return [];

        const quotes = result.quotes.filter(q => q.close !== null);
        if (interval === '1h') {
            return quotes.map(q => ({
                openTime: q.date.getTime(),
                open: q.open,
                high: q.high,
                low: q.low,
                close: q.close,
                volume: q.volume || 0,
                closeTime: q.date.getTime() + 60 * 60 * 1000 - 1
            }));
        }

        // Aggregate to 4h
        const intervalMs = 4 * 60 * 60 * 1000;
        const candles = [];
        const groups = {};

        for (const q of quotes) {
            const ts = q.date.getTime();
            const bucket = Math.floor(ts / intervalMs) * intervalMs;
            if (!groups[bucket]) groups[bucket] = [];
            groups[bucket].push(q);
        }

        const sortedBuckets = Object.keys(groups).sort((a, b) => parseInt(a) - parseInt(b));
        for (const bucketStr of sortedBuckets) {
            const bucket = parseInt(bucketStr);
            const qList = groups[bucket];
            candles.push({
                openTime: bucket,
                open: qList[0].open,
                high: Math.max(...qList.map(q => q.high)),
                low: Math.min(...qList.map(q => q.low)),
                close: qList[qList.length - 1].close,
                volume: qList.reduce((sum, q) => sum + (q.volume || 0), 0),
                closeTime: bucket + intervalMs - 1
            });
        }

        return candles;
    } catch (err) {
        console.error(`[Backfill] Yahoo Failed for ${symbol}:`, err.message);
        return [];
    }
}
async function fetchKlinesFromPionex(symbol, interval, limit = 500) {
    const pionexSymbol = PIONEX_SYMBOL_MAP[symbol];
    if (!pionexSymbol) return [];

    // Pionex intervals (uppercase): 1M, 5M, 15M, 30M, 60M, 4H, 1D
    const intervalMap = { '1h': '60M', '4h': '4H', '1d': '1D' };
    const pionexInterval = intervalMap[interval] || '4H';
    const pionexLimit = Math.min(limit, 500);

    const url = `https://api.pionex.com/api/v1/market/klines?symbol=${pionexSymbol}&interval=${pionexInterval}&limit=${pionexLimit}`;

    try {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`Pionex API ${res.status}`);
        const json = await res.json();
        if (!json.result || !json.data?.klines) {
            console.error(`[Backfill] Pionex error for ${symbol}:`, json.message);
            return [];
        }
        // Pionex returns newest first; reverse to oldest first
        const list = [...json.data.klines].reverse();
        const intervalMs = interval === '4h' ? 4 * 3600 * 1000 : 3600 * 1000;
        return list.map(k => ({
            openTime: parseInt(k.time),
            open: parseFloat(k.open),
            high: parseFloat(k.high),
            low: parseFloat(k.low),
            close: parseFloat(k.close),
            volume: parseFloat(k.volume),
            closeTime: parseInt(k.time) + intervalMs - 1
        }));
    } catch (err) {
        console.error(`[Backfill] Pionex fetch failed for ${symbol}:`, err.message);
        return [];
    }
}

async function fetchKlinesFromCG(symbol, interval, days) {
    const cgId = CG_ID_MAP[symbol];
    if (!cgId) return [];

    const baseUrl = `https://api.coingecko.com/api/v3/coins/${cgId}/market_chart?vs_currency=usd&days=${days}`;

    console.log(`[Backfill] Fetching ${symbol} from CoinGecko (${days} days)...`);

    try {
        const res = await fetch(baseUrl, { headers: { 'User-Agent': 'Mozilla/5.0' } });
        if (!res.ok) throw new Error(`CG API error: ${res.status}`);
        const data = await res.json();

        if (!data.prices || data.prices.length === 0) return [];

        // CG returns [ts, price]. We need to convert/group into candles.
        const candles = [];
        const intervalMs = (interval === '4h' ? 4 : 1) * 60 * 60 * 1000;

        // Group points by interval
        const groups = {};
        for (const [ts, price] of data.prices) {
            // Data Sanitization: Ignore zero or invalid prices (common in some CG token feeds)
            if (!price || price < 1) continue;

            const bucket = Math.floor(ts / intervalMs) * intervalMs;
            if (!groups[bucket]) groups[bucket] = [];
            groups[bucket].push(price);
        }

        const sortedBuckets = Object.keys(groups).sort((a, b) => parseInt(a) - parseInt(b));

        for (const bucketStr of sortedBuckets) {
            const bucket = parseInt(bucketStr);
            const prices = groups[bucket];
            candles.push({
                openTime: bucket,
                open: prices[0],
                high: Math.max(...prices),
                low: Math.min(...prices),
                close: prices[prices.length - 1],
                volume: 0,
                closeTime: bucket + intervalMs - 1
            });
        }

        return candles;
    } catch (err) {
        console.error(`[Backfill] CG Failed for ${symbol}:`, err.message);
        return [];
    }
}

// Backfill a single symbol with pagination
async function backfillSymbol(symbol, timeframe) {
    const isCG = !!CG_ID_MAP[symbol];
    const isYahoo = !!YAHOO_SYMBOL_MAP[symbol];
    const isPionex = !!PIONEX_SYMBOL_MAP[symbol];
    const latestTime = await getLatestCandleTime(symbol, timeframe);
    const now = Date.now();
    let startTime;

    if (isCG || (isYahoo && !latestTime) || isPionex) {
        // For CG or initial Yahoo, fetch a window
        const days = 90;
        startTime = now - (days * 24 * 60 * 60 * 1000);
        const candles = await fetchKlines(symbol, timeframe, startTime, now);
        if (candles.length > 0) {
            const count = await insertCandles(symbol, timeframe, candles);
            console.log(`[Backfill] ${symbol} (${timeframe}): Inserted ${count} candles from ${isCG ? 'CoinGecko' : isPionex ? 'Pionex' : 'Yahoo'}`);
        }
        return;
    }

    if (latestTime) {
        // Incremental: start from last known candle
        startTime = latestTime + 1;
        console.log(`[Backfill] ${symbol}: Incremental from ${new Date(startTime).toISOString()}`);
    } else {
        // Full backfill
        startTime = now - (BACKFILL_DAYS * 24 * 60 * 60 * 1000);
        console.log(`[Backfill] ${symbol}: Full backfill (${BACKFILL_DAYS} days)`);
    }

    let totalInserted = 0;
    let currentStart = startTime;

    while (currentStart < now) {
        const candles = await fetchKlines(symbol, timeframe, currentStart, now, 1000);

        if (candles.length === 0) break;

        const count = await insertCandles(symbol, timeframe, candles);
        totalInserted += count;

        // Move to next batch
        const lastCandle = candles[candles.length - 1];
        currentStart = lastCandle.closeTime + 1;

        // Rate limiting
        await new Promise(r => setTimeout(r, 200));
    }

    const totalCount = await getCandleCount(symbol, timeframe);
    console.log(`[Backfill] ${symbol} (${timeframe}): Inserted ${totalInserted} candles (total in DB: ${totalCount})`);
    return totalInserted;
}

async function backfillAllSymbols() {
    for (const timeframe of TIMEFRAMES) {
        for (const symbol of SYMBOLS) {
            try {
                await backfillSymbol(symbol, timeframe);
            } catch (err) {
                console.error(`[Backfill] Error for ${symbol} (${timeframe}):`, err.message);
            }
        }
    }
}

// Scheduled sync - run every 4 hours
function startScheduledSync() {
    const FOUR_HOURS = 4 * 60 * 60 * 1000;
    setInterval(async () => {
        console.log('[Sync] Running scheduled data sync...');
        await backfillAllSymbols();
        console.log('[Sync] Scheduled sync complete');
    }, FOUR_HOURS);
}

module.exports = {
    backfillAllSymbols,
    backfillSymbol,
    startScheduledSync,
    fetchKlines,
    SYMBOLS,
    TIMEFRAMES
};
