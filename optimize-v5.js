/**
 * optimize-v5.js
 * V5 穩定版參數最佳化：V4b + EMA200方向 + EMA斜率 + ADX加強 + 連虧保護
 * 評分權重：降低MDD最重要，報酬次之
 */

const fs = require('fs');
const path = require('path');
const { CCI, EMA, ADX } = require('./cryptobot/node_modules/technicalindicators');

function loadCSV(filePath) {
  const raw = fs.readFileSync(filePath, 'utf8').trim();
  return raw.split('\n').slice(1).map(line => {
    const c = line.split(',');
    return { time: +c[0], open: +c[1], high: +c[2], low: +c[3], close: +c[4] };
  });
}

function calcATR(candles, period) {
  const atr = [];
  for (let i = 0; i < candles.length; i++) {
    const tr = i === 0 ? candles[i].high - candles[i].low :
      Math.max(candles[i].high - candles[i].low, Math.abs(candles[i].high - candles[i-1].close), Math.abs(candles[i].low - candles[i-1].close));
    if (i < period - 1) { atr.push(tr); continue; }
    if (i === period - 1) { let s = 0; for (let j = 0; j <= i; j++) s += (j < period ? (j === 0 ? candles[j].high-candles[j].low : Math.max(candles[j].high-candles[j].low, Math.abs(candles[j].high-candles[j-1].close), Math.abs(candles[j].low-candles[j-1].close))) : 0); atr.push(s/period); }
    else { atr.push((atr[atr.length-1] * (period-1) + tr) / period); }
  }
  return atr;
}

function calcPseudoMFI(candles, period) {
  const results = [];
  for (let i = period; i < candles.length; i++) {
    let pos = 0, neg = 0;
    for (let j = i - period + 1; j <= i; j++) {
      const range = candles[j].high - candles[j].low;
      if (range === 0) continue;
      const dir = (candles[j].close - candles[j].open) / range;
      if (dir > 0) pos += dir * range; else neg += Math.abs(dir) * range;
    }
    const total = pos + neg;
    results.push(total > 0 ? (pos / total) * 100 : 50);
  }
  return results;
}

function preCompute(candles) {
  const closes = candles.map(c => c.close);
  const highs = candles.map(c => c.high);
  const lows = candles.map(c => c.low);
  const cache = {};

  for (const p of [14]) cache[`cci_${p}`] = CCI.calculate({ high: highs, low: lows, close: closes, period: p });
  for (const p of [25, 30]) cache[`cci_${p}`] = CCI.calculate({ high: highs, low: lows, close: closes, period: p });
  for (const p of [60, 80, 100]) cache[`cci_${p}`] = CCI.calculate({ high: highs, low: lows, close: closes, period: p });

  for (const p of [50]) cache[`ema_${p}`] = EMA.calculate({ period: p, values: closes });
  for (const p of [100, 200]) cache[`emaSlow_${p}`] = EMA.calculate({ period: p, values: closes });
  for (const p of [20, 30, 40]) cache[`emaTrail_${p}`] = EMA.calculate({ period: p, values: closes });

  cache.atr14 = calcATR(candles, 14);
  cache.mfi14 = calcPseudoMFI(candles, 14);

  try { cache.adx_14 = ADX.calculate({ high: highs, low: lows, close: closes, period: 14 }); } catch(e) { cache.adx_14 = []; }

  return cache;
}

function getVal(arr, i, totalLen) {
  const idx = i - (totalLen - arr.length);
  return idx >= 0 && idx < arr.length ? arr[idx] : null;
}

function backtest(candles, cache, params) {
  const { cciMid, cciSlow, cciThreshold, cciSlowTh, emaTrail, atrSL, atrTP, minScore, cooldown, adxMin, emaSlow, emaSlopeLen, emaSlopeMin, useEMA200, useSlope, useLossLimit, maxConsecLoss, lossPauseBars } = params;

  const cciFastArr = cache.cci_14;
  const cciMidArr = cache[`cci_${cciMid}`];
  const cciSlowArr = cache[`cci_${cciSlow}`];
  const emaTrendArr = cache.ema_50;
  const emaTrailArr = cache[`emaTrail_${emaTrail}`];
  const emaSlowArr = cache[`emaSlow_${emaSlow}`];
  const atrArr = cache.atr14;
  const mfiArr = cache.mfi14;
  const adxArr = cache.adx_14;

  if (!cciFastArr || !cciMidArr || !cciSlowArr || !emaTrendArr || !emaTrailArr || !emaSlowArr) return null;

  const n = candles.length;
  const startBar = Math.max(emaSlow, 80) + 20;

  let balance = 100000, position = null, trades = [], barsSinceExit = 999;
  let peakBalance = 100000, maxDD = 0;
  let consecLosses = 0, lossPauseUntil = 0;
  let equityHistory = [];

  for (let i = startBar; i < n; i++) {
    const c = candles[i];
    const price = c.close;

    const cciFast = getVal(cciFastArr, i, n);
    const cciFastPrev = getVal(cciFastArr, i-1, n);
    const cciMidV = getVal(cciMidArr, i, n);
    const cciSlowV = getVal(cciSlowArr, i, n);
    const emaT = getVal(emaTrendArr, i, n);
    const emaTrailV = getVal(emaTrailArr, i, n);
    const emaSlowV = getVal(emaSlowArr, i, n);
    const emaTprev = getVal(emaTrendArr, i - emaSlopeLen, n);
    const atr = getVal(atrArr, i, n);
    const mfi = getVal(mfiArr, i, n);
    const adxObj = getVal(adxArr, i, n);
    const adx = adxObj ? adxObj.adx : 30;

    if (cciFast === null || cciFastPrev === null || cciMidV === null || cciSlowV === null || emaT === null || emaTrailV === null || emaSlowV === null || atr === null || mfi === null || emaTprev === null) continue;

    const adxOK = adx >= adxMin;
    const atrOK = atr >= 5;

    // EMA slope
    const slope = (emaT - emaTprev) / emaSlopeLen;
    const slopeOK = !useSlope || Math.abs(slope) >= emaSlopeMin;
    const ema200Bull = !useEMA200 || price > emaSlowV;
    const ema200Bear = !useEMA200 || price < emaSlowV;
    const lossPauseOK = !useLossLimit || i >= lossPauseUntil;

    // Position management
    if (position) {
      barsSinceExit = 0;
      const isLong = position.side === 'long';

      // SL check
      if (isLong && c.low <= position.sl) {
        const pnl = position.sl - position.entry;
        balance += pnl; trades.push({ pnl }); position = null; barsSinceExit = 0;
        consecLosses++;
        if (useLossLimit && consecLosses >= maxConsecLoss) { lossPauseUntil = i + lossPauseBars; consecLosses = 0; }
        continue;
      }
      if (!isLong && c.high >= position.sl) {
        const pnl = position.entry - position.sl;
        balance += pnl; trades.push({ pnl }); position = null; barsSinceExit = 0;
        consecLosses++;
        if (useLossLimit && consecLosses >= maxConsecLoss) { lossPauseUntil = i + lossPauseBars; consecLosses = 0; }
        continue;
      }

      // Trailing
      if (isLong) {
        const fp = price - position.entry;
        if (!position.trailingActive && fp >= position.atr * atrTP) { position.trailingActive = true; position.trailingSL = emaTrailV; }
        if (position.trailingActive) { position.trailingSL = Math.max(position.trailingSL, emaTrailV); if (c.low <= position.trailingSL) { balance += position.trailingSL - position.entry; trades.push({ pnl: position.trailingSL - position.entry }); position = null; barsSinceExit = 0; consecLosses = 0; continue; } }
      } else {
        const fp = position.entry - price;
        if (!position.trailingActive && fp >= position.atr * atrTP) { position.trailingActive = true; position.trailingSL = emaTrailV; }
        if (position.trailingActive) { position.trailingSL = Math.min(position.trailingSL, emaTrailV); if (c.high >= position.trailingSL) { balance += position.entry - position.trailingSL; trades.push({ pnl: position.entry - position.trailingSL }); position = null; barsSinceExit = 0; consecLosses = 0; continue; } }
      }

      // CCI exit
      if (isLong && cciFast < 0 && cciFastPrev >= 0) {
        const pnl = price - position.entry;
        balance += pnl; trades.push({ pnl }); position = null; barsSinceExit = 0;
        if (pnl < 0) { consecLosses++; if (useLossLimit && consecLosses >= maxConsecLoss) { lossPauseUntil = i + lossPauseBars; consecLosses = 0; } }
        else consecLosses = 0;
        continue;
      }
      if (!isLong && cciFast > 0 && cciFastPrev <= 0) {
        const pnl = position.entry - price;
        balance += pnl; trades.push({ pnl }); position = null; barsSinceExit = 0;
        if (pnl < 0) { consecLosses++; if (useLossLimit && consecLosses >= maxConsecLoss) { lossPauseUntil = i + lossPauseBars; consecLosses = 0; } }
        else consecLosses = 0;
        continue;
      }

    } else {
      barsSinceExit++;
      if (!cooldownOK(barsSinceExit, cooldown) || !atrOK || !adxOK || !slopeOK || !lossPauseOK) continue;

      // Long
      const lCCI1 = cciFastPrev <= 0 && cciFast > 0 && cciFast > cciThreshold;
      const lCCI2 = cciMidV > 0;
      const lCCI3 = cciSlowV > cciSlowTh;
      const lTrend = price > emaT;
      const lMFI = mfi > 40;
      const lScore = (lCCI1?1:0)+(lCCI2?1:0)+(lCCI3?1:0)+(lTrend?1:0)+(lMFI?1:0);

      if (lScore >= minScore && lCCI1 && ema200Bull) {
        position = { side:'long', entry:price, sl:price-atr*atrSL, atr, trailingActive:false, trailingSL:0 };
        barsSinceExit = 0; continue;
      }

      // Short
      const sCCI1 = cciFastPrev >= 0 && cciFast < 0 && cciFast < -cciThreshold;
      const sCCI2 = cciMidV < 0;
      const sCCI3 = cciSlowV < -cciSlowTh;
      const sTrend = price < emaT;
      const sMFI = mfi < 60;
      const sScore = (sCCI1?1:0)+(sCCI2?1:0)+(sCCI3?1:0)+(sTrend?1:0)+(sMFI?1:0);

      if (sScore >= minScore && sCCI1 && ema200Bear) {
        position = { side:'short', entry:price, sl:price+atr*atrSL, atr, trailingActive:false, trailingSL:Infinity };
        barsSinceExit = 0;
      }
    }

    if (balance > peakBalance) peakBalance = balance;
    const dd = peakBalance > 0 ? (peakBalance - balance) / peakBalance : 0;
    if (dd > maxDD) maxDD = dd;

    // Record equity every 100 bars for stability analysis
    if (i % 100 === 0) equityHistory.push(balance);
  }

  // Close open
  if (position) {
    const lp = candles[n-1].close;
    const pnl = position.side === 'long' ? lp - position.entry : position.entry - lp;
    balance += pnl; trades.push({ pnl });
  }

  const wins = trades.filter(t => t.pnl > 0);
  const losses = trades.filter(t => t.pnl <= 0);
  const grossProfit = wins.reduce((s,t) => s+t.pnl, 0);
  const grossLoss = Math.abs(losses.reduce((s,t) => s+t.pnl, 0));

  // Stability score: equity curve smoothness
  let stabilityScore = 0;
  if (equityHistory.length > 5) {
    let negPeriods = 0;
    for (let j = 1; j < equityHistory.length; j++) {
      if (equityHistory[j] < equityHistory[j-1]) negPeriods++;
    }
    stabilityScore = 1 - (negPeriods / (equityHistory.length - 1));
  }

  return {
    trades: trades.length, wins: wins.length,
    winRate: trades.length > 0 ? wins.length / trades.length * 100 : 0,
    pnl: balance - 100000,
    returnPct: (balance - 100000) / 100000 * 100,
    pf: grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? 99 : 0,
    maxDD: maxDD * 100,
    stability: stabilityScore,
    avgWin: wins.length > 0 ? grossProfit / wins.length : 0,
    avgLoss: losses.length > 0 ? grossLoss / losses.length : 0,
  };
}

function cooldownOK(bars, cd) { return bars >= cd; }

// ─── Main ───
const candles = loadCSV(path.join(__dirname, 'COMEX_GC1!, 60.csv'));
console.log(`Loaded ${candles.length} candles\n`);
const cache = preCompute(candles);

const grid = {
  cciMid: [25, 30],
  cciSlow: [80, 100],
  cciThreshold: [50],
  cciSlowTh: [30, 50],
  emaTrail: [20, 30, 40],
  atrSL: [2.0, 2.5, 3.0],
  atrTP: [2.5, 2.8, 3.5],
  minScore: [4, 5],
  cooldown: [2, 3],
  adxMin: [15, 20, 25],
  emaSlow: [100, 200],
  emaSlopeLen: [10],
  emaSlopeMin: [0, 0.3, 0.5],
  useEMA200: [true, false],
  useSlope: [true, false],
  useLossLimit: [true, false],
  maxConsecLoss: [3],
  lossPauseBars: [10],
};

let combos = [{}];
for (const [key, values] of Object.entries(grid)) {
  const nc = [];
  for (const combo of combos) for (const val of values) nc.push({ ...combo, [key]: val });
  combos = nc;
}
// Remove contradictions
combos = combos.filter(c => {
  if (!c.useSlope && c.emaSlopeMin > 0) return false;
  if (!c.useLossLimit && c.maxConsecLoss !== 3) return false;
  return true;
});

console.log(`Testing ${combos.length} combinations...\n`);

const results = [];
let tested = 0;
for (const p of combos) {
  const r = backtest(candles, cache, p);
  if (r && r.trades >= 20) {
    // Stability-focused scoring: MDD penalty is heavy
    const score = r.returnPct * 0.3 + r.pf * 8 + r.winRate * 0.4 - r.maxDD * 0.8 + r.stability * 15;
    results.push({ ...p, ...r, score });
  }
  tested++;
  if (tested % 2000 === 0) process.stdout.write(`  ${tested}/${combos.length}\r`);
}

console.log(`\n${results.length} valid results\n`);
results.sort((a, b) => b.score - a.score);

console.log('═══════════════════════════════════════════════════════════════');
console.log('  TOP 10 — STABILITY FOCUSED (Low MDD + Steady Returns)');
console.log('═══════════════════════════════════════════════════════════════\n');

for (let i = 0; i < Math.min(10, results.length); i++) {
  const r = results[i];
  console.log(`#${i+1} Score: ${r.score.toFixed(1)}`);
  console.log(`  CCI: 14/${r.cciMid}/${r.cciSlow}  Th:${r.cciThreshold}  SlowTh:${r.cciSlowTh}  Score≥${r.minScore}`);
  console.log(`  SL:${r.atrSL}  TP:${r.atrTP}  Trail:${r.emaTrail}  ADX≥${r.adxMin}  CD:${r.cooldown}`);
  console.log(`  EMA200:${r.emaSlow}${r.useEMA200?' ON':' OFF'}  Slope:${r.useSlope?r.emaSlopeMin:'OFF'}  LossLimit:${r.useLossLimit?'ON':'OFF'}`);
  console.log(`  → Ret:${r.returnPct.toFixed(1)}% PF:${r.pf.toFixed(2)} WR:${r.winRate.toFixed(1)}% Trades:${r.trades} MDD:${r.maxDD.toFixed(1)}% Stab:${r.stability.toFixed(2)}`);
  console.log('');
}

// Best by lowest MDD with positive return
console.log('═══ LOWEST MDD (Return > 0) ═══');
const byMDD = results.filter(r => r.returnPct > 0).sort((a, b) => a.maxDD - b.maxDD).slice(0, 5);
for (const r of byMDD) {
  console.log(`  MDD:${r.maxDD.toFixed(1)}% Ret:${r.returnPct.toFixed(1)}% PF:${r.pf.toFixed(2)} WR:${r.winRate.toFixed(1)}% Trades:${r.trades} | ADX≥${r.adxMin} SL:${r.atrSL} EMA200:${r.useEMA200?r.emaSlow:'OFF'} Slope:${r.useSlope?r.emaSlopeMin:'OFF'} Loss:${r.useLossLimit?'ON':'OFF'}`);
}

console.log('\n═══ BEST RETURN (MDD < 5%) ═══');
const byRet = results.filter(r => r.maxDD < 5).sort((a, b) => b.returnPct - a.returnPct).slice(0, 5);
for (const r of byRet) {
  console.log(`  Ret:${r.returnPct.toFixed(1)}% MDD:${r.maxDD.toFixed(1)}% PF:${r.pf.toFixed(2)} WR:${r.winRate.toFixed(1)}% Trades:${r.trades} | ADX≥${r.adxMin} SL:${r.atrSL} EMA200:${r.useEMA200?r.emaSlow:'OFF'} Slope:${r.useSlope?r.emaSlopeMin:'OFF'}`);
}

console.log('\n═══ BEST WIN RATE (≥30 trades) ═══');
const byWR = results.filter(r => r.trades >= 30).sort((a, b) => b.winRate - a.winRate).slice(0, 5);
for (const r of byWR) {
  console.log(`  WR:${r.winRate.toFixed(1)}% Ret:${r.returnPct.toFixed(1)}% PF:${r.pf.toFixed(2)} MDD:${r.maxDD.toFixed(1)}% Trades:${r.trades} | ADX≥${r.adxMin} SL:${r.atrSL} Score≥${r.minScore}`);
}

console.log('\n═══ BEST PROFIT FACTOR (≥30 trades) ═══');
const byPF = results.filter(r => r.trades >= 30).sort((a, b) => b.pf - a.pf).slice(0, 5);
for (const r of byPF) {
  console.log(`  PF:${r.pf.toFixed(2)} Ret:${r.returnPct.toFixed(1)}% WR:${r.winRate.toFixed(1)}% MDD:${r.maxDD.toFixed(1)}% Trades:${r.trades} | ADX≥${r.adxMin} SL:${r.atrSL} EMA200:${r.useEMA200?r.emaSlow:'OFF'}`);
}
