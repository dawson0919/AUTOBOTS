// Real-time Price Monitor via Binance WebSocket
const WebSocket = require('ws');
const { insertCandles, getLatestClosePrice } = require('./database');

const SPOT_SYMBOLS = ['btcusdt', 'ethusdt', 'solusdt', 'paxgusdt'];
// CRITIAL FIX: Do NOT subscribe to SPXUSDT/NASUSDT on Binance WebSocket.
// Binance SPXUSDT is an old/delisted pair stuck at ~$0.32, causing massive errors.
// Gold (XAUUSDT) is fine on Binance Futures. Use DB/CoinGecko for indices.
const FUTURES_SYMBOLS = ['xauusdt'];
const ALL_SYMBOLS = [...SPOT_SYMBOLS, ...FUTURES_SYMBOLS];
const TIMEFRAMES = ['1h', '4h'];
const currentPrices = {};
let wsConnection = null;
let reconnectAttempts = 0;
const MAX_RECONNECT_DELAY = 30000;

function getCurrentPrices() {
    return { ...currentPrices };
}

function startPriceMonitor() {
    const spotStreams = SPOT_SYMBOLS.map(s => [`${s}@kline_4h`, `${s}@kline_1h`, `${s}@ticker`]).flat().join('/');
    const futuresStreams = FUTURES_SYMBOLS.map(s => [`${s}@kline_4h`, `${s}@kline_1h`, `${s}@ticker`]).flat().join('/');

    const spotUrl = `wss://stream.binance.com:9443/stream?streams=${spotStreams}`;
    const futuresUrl = `wss://fstream.binance.com/stream?streams=${futuresStreams}`;

    function connect(url, isFutures) {
        console.log(`[PriceMonitor] Connecting to Binance ${isFutures ? 'Futures' : 'Spot'} WebSocket...`);
        let ws;
        try {
            ws = new WebSocket(url);
        } catch (err) {
            console.error(`[PriceMonitor] ${isFutures ? 'Futures' : 'Spot'} Connection error:`, err.message);
            setTimeout(() => connect(url, isFutures), 5000);
            return;
        }

        ws.on('open', () => {
            console.log(`[PriceMonitor] Connected to Binance ${isFutures ? 'Futures' : 'Spot'} WebSocket`);
        });

        ws.on('message', async (rawData) => {
            try {
                const msg = JSON.parse(rawData.toString());
                const data = msg.data;
                if (!data) return;

                const stream = msg.stream || '';

                // Ticker data - update current prices
                if (stream.includes('@ticker')) {
                    const symbol = data.s; // e.g. BTCUSDT
                    currentPrices[symbol] = {
                        price: parseFloat(data.c),
                        change24h: parseFloat(data.P),
                        high24h: parseFloat(data.h),
                        low24h: parseFloat(data.l),
                        volume24h: parseFloat(data.v),
                        timestamp: Date.now()
                    };

                    // Broadcast to frontend clients
                    if (global.broadcastPrices) {
                        global.broadcastPrices(currentPrices);
                    }
                }

                // Kline data - save closed candles to DB
                // Kline data - save closed candles to DB
                if (stream.includes('@kline_') && data.k) {
                    const kline = data.k;
                    const interval = kline.i;

                    // Only save when candle is closed
                    if (kline.x === true) {
                        const symbol = kline.s;
                        const candle = {
                            openTime: kline.t,
                            open: parseFloat(kline.o),
                            high: parseFloat(kline.h),
                            low: parseFloat(kline.l),
                            close: parseFloat(kline.c),
                            volume: parseFloat(kline.v),
                            closeTime: kline.T
                        };

                        try {
                            await insertCandles(symbol, interval, [candle]);
                            console.log(`[PriceMonitor] Saved closed ${interval} candle for ${symbol}`);
                        } catch (err) {
                            console.error(`[PriceMonitor] Failed to save ${interval} candle for ${symbol}:`, err.message);
                        }
                    }
                }
            } catch (err) {
                // Silently skip parse errors
            }
        });

        ws.on('error', (err) => {
            console.error(`[PriceMonitor] ${isFutures ? 'Futures' : 'Spot'} WebSocket error:`, err.message);
        });

        ws.on('close', () => {
            console.log(`[PriceMonitor] ${isFutures ? 'Futures' : 'Spot'} WebSocket disconnected, reconnecting...`);
            setTimeout(() => connect(url, isFutures), 5000);
        });
    }

    connect(spotUrl, false);
    connect(futuresUrl, true);

    // Poll Index Prices (DB first, then CoinGecko fallback)
    async function pollCoinGecko() {
        // 1. Fetch from Database (Primary Source for Indices if updated)
        for (const symbol of ['SPXUSDT', 'NASUSDT']) {
            try {
                const dbPrice = await getLatestClosePrice(symbol);
                if (dbPrice && dbPrice > 100) { // Valid index price check (> $100)
                    currentPrices[symbol] = {
                        price: dbPrice,
                        change24h: 0, // Difficult to calculate live change from just one DB close, assume 0 for ticker stability
                        timestamp: Date.now(),
                        source: 'database'
                    };
                }
            } catch (e) {
                console.error(`[PriceMonitor] DB fetch error for ${symbol}:`, e.message);
            }
        }

        // 2. Fetch from CoinGecko (Secondary/Backup)
        const https = require('https');
        const cgIds = {
            'SPXUSDT': 'spdr-s-p-500-etf-ondo-tokenized-etf',
            'NASUSDT': 'invesco-qqq-etf-ondo-tokenized-etf'
        };
        const ids = Object.values(cgIds).join(',');
        const options = {
            hostname: 'api.coingecko.com',
            path: `/api/v3/simple/price?ids=${ids}&vs_currencies=usd&include_24hr_change=true`,
            headers: { 'User-Agent': 'Mozilla/5.0' }
        };

        https.get(options, res => {
            let data = '';
            res.on('data', d => data += d);
            res.on('end', () => {
                try {
                    const json = JSON.parse(data);
                    Object.entries(cgIds).forEach(([symbol, id]) => {
                        if (json[id]) {
                            const cgPrice = json[id].usd;
                            // Only overwrite DB price if CoinGecko price looks valid (real index > 100)
                            // Tokenized ETFs can depeg or error to ~$0.32
                            if (cgPrice > 100) {
                                currentPrices[symbol] = {
                                    price: cgPrice,
                                    change24h: json[id].usd_24h_change || 0,
                                    timestamp: Date.now(),
                                    source: 'coingecko'
                                };
                            }
                        }
                    });

                    if (global.broadcastPrices) {
                        global.broadcastPrices(currentPrices);
                    }
                } catch (e) {
                    console.error('[PriceMonitor] CoinGecko parse error:', e.message);
                }
            });
        }).on('error', e => {
            console.error('[PriceMonitor] CoinGecko fetch error:', e.message);
        });
    }

    // Poll WTI Crude Oil (CLUSDT) from Pionex Perpetual
    async function pollPionexFutures() {
        const pionexMap = { 'CLUSDT': 'WTI_USDT_PERP' };
        const https = require('https');
        for (const [symbol, pionexSymbol] of Object.entries(pionexMap)) {
            const url = `https://api.pionex.com/api/v1/market/tickers?symbol=${pionexSymbol}`;
            https.get(url, res => {
                let data = '';
                res.on('data', d => data += d);
                res.on('end', () => {
                    try {
                        const json = JSON.parse(data);
                        const t = json.data?.tickers?.[0];
                        if (t && parseFloat(t.close) > 5) {
                            const open = parseFloat(t.open) || 0;
                            const close = parseFloat(t.close);
                            const changePct = open > 0 ? ((close - open) / open) * 100 : 0;
                            currentPrices[symbol] = {
                                price: close,
                                change24h: changePct,
                                high24h: parseFloat(t.high) || 0,
                                low24h: parseFloat(t.low) || 0,
                                volume24h: parseFloat(t.volume) || 0,
                                timestamp: Date.now(),
                                source: 'pionex'
                            };
                            if (global.broadcastPrices) global.broadcastPrices(currentPrices);
                        }
                    } catch (e) {
                        console.error(`[PriceMonitor] Pionex parse error for ${symbol}:`, e.message);
                    }
                });
            }).on('error', e => console.error(`[PriceMonitor] Pionex fetch error for ${symbol}:`, e.message));
        }
    }

    // Poll NQ/ES Futures Prices from Yahoo Finance (15min delay)
    async function pollYahooFutures() {
        const yahooMap = { 'NQUSDT': 'NQ=F', 'ESUSDT': 'ES=F' };
        try {
            const YahooFinance = require('yahoo-finance2').default;
            const yahoo = new YahooFinance();
            for (const [symbol, yahooSymbol] of Object.entries(yahooMap)) {
                try {
                    const quote = await yahoo.quote(yahooSymbol);
                    if (quote && quote.regularMarketPrice > 100) {
                        currentPrices[symbol] = {
                            price: quote.regularMarketPrice,
                            change24h: quote.regularMarketChangePercent || 0,
                            high24h: quote.regularMarketDayHigh || 0,
                            low24h: quote.regularMarketDayLow || 0,
                            volume24h: quote.regularMarketVolume || 0,
                            timestamp: Date.now(),
                            source: 'yahoo'
                        };
                    }
                } catch (e) {
                    console.error(`[PriceMonitor] Yahoo fetch error for ${symbol}:`, e.message);
                }
            }
            if (global.broadcastPrices) {
                global.broadcastPrices(currentPrices);
            }
        } catch (e) {
            console.error('[PriceMonitor] Yahoo module error:', e.message);
        }
    }

    // Initial fetch and 60s interval
    pollCoinGecko();
    setInterval(pollCoinGecko, 60000);
    pollYahooFutures();
    setInterval(pollYahooFutures, 60000);
    pollPionexFutures();
    setInterval(pollPionexFutures, 60000);
}

function stopPriceMonitor() {
    if (wsConnection) {
        wsConnection.close();
        wsConnection = null;
    }
}

module.exports = {
    startPriceMonitor,
    stopPriceMonitor,
    getCurrentPrices
};
