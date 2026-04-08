/**
 * optimize-v4b.js
 * V4b 參數最佳化：V3 邏輯 + ADX 過濾
 * 測試 ADX 門檻、CCI 參數、SL/TP、minScore 的最佳組合
 */

const fs = require('fs');
const path = require('path');
const { CCI, EMA, ADX } = require('./cryptobot/node_modules/technicalindicators');

// ─── CSV ───
function loadCSV(filePath) {
  const raw = fs.readFileSync(filePath, 'utf8').trim();
  return raw.split('\n').slice(1).map(line => {
    const c = line.split(',');
    return { time: +c[0], open: +c[1], high: +c[2], low: +c[3], close: +c[4] };
  });
}

// ─── ATR ───
function calcATR(candles, period) {
  const result = [];
  for (let i = 0; i < candles.length; i++) {
    if (i === 0) { result.push(candles[i].high - candles[i].low); continue; }
    const tr = Math.max(
      candles[i].high - candles[i].low,
      Math.abs(candles[i].high - candles[i - 1].close),
      Math.abs(candles[i].low - candles[i - 1].close)
    );
    result.push(tr);
  }
  const atr = [];
  let sum = 0;
  for (let i = 0; i < result.length; i++) {
    sum += result[i];
    if (i >= period - 1) {
      if (i === period - 1) atr.push(sum / period);
      else { atr.push((atr[atr.length - 1] * (period - 1) + result[i]) / period); }
      if (i >= period) sum -= result[i - period + 1]; // not exact but close enough for opt
    }
  }
  return atr;
}

// ─── Pseudo MFI ───
function calcPseudoMFI(candles, period) {
  const results = [];
  for (let i = period; i < candles.length; i++) {
    let pos = 0, neg = 0;
    for (let j = i - period + 1; j <= i; j++) {
      const range = candles[j].high - candles[j].low;
      if (range === 0) continue;
      const dir = (candles[j].close - candles[j].open) / range;
      if (dir > 0) pos += dir * range;
      else neg += Math.abs(dir) * range;
    }
    const total = pos + neg;
    results.push(total > 0 ? (pos / total) * 100 : 50);
  }
  return results;
}

// ─── ADX calc ───
function calcADX(candles, period) {
  const high = candles.map(c => c.high);
  const low = candles.map(c => c.low);
  const close = candles.map(c => c.close);
  try {
    return ADX.calculate({ high, low, close, period });
  } catch (e) {
    return [];
  }
}

// ─── Pre-compute all indicators for speed ───
function preCompute(candles) {
  const closes = candles.map(c => c.close);
  const highs = candles.map(c => c.high);
  const lows = candles.map(c => c.low);

  const cache = {};

  // CCI variations
  for (const p of [10, 14, 20]) {
    cache[`cci_${p}`] = CCI.calculate({ high: highs, low: lows, close: closes, period: p });
  }
  for (const p of [25, 30, 40]) {
    cache[`cci_${p}`] = CCI.calculate({ high: highs, low: lows, close: closes, period: p });
  }
  for (const p of [50, 60, 80, 100]) {
    cache[`cci_${p}`] = CCI.calculate({ high: highs, low: lows, close: closes, period: p });
  }

  // EMA variations
  for (const p of [30, 40, 50, 60]) {
    cache[`ema_${p}`] = EMA.calculate({ period: p, values: closes });
  }
  for (const p of [10, 15, 20, 30, 40]) {
    cache[`emaTrail_${p}`] = EMA.calculate({ period: p, values: closes });
  }

  // ATR
  cache.atr14 = calcATR(candles, 14);

  // MFI
  cache.mfi14 = calcPseudoMFI(candles, 14);

  // ADX variations
  for (const p of [14]) {
    cache[`adx_${p}`] = calcADX(candles, p);
  }

  return cache;
}

// ─── Backtest engine ───
function backtest(candles, cache, params) {
  const {
    cciFast, cciMid, cciSlow, cciThreshold, cciSlowTh,
    emaTrend, emaTrail, atrSL, atrTP,
    minScore, cooldown, adxMin, useADX
  } = params;

  const cciFastArr = cache[`cci_${cciFast}`];
  const cciMidArr = cache[`cci_${cciMid}`];
  const cciSlowArr = cache[`cci_${cciSlow}`];
  const emaTrendArr = cache[`ema_${emaTrend}`];
  const emaTrailArr = cache[`emaTrail_${emaTrail}`] || cache[`ema_${emaTrail}`];
  const atrArr = cache.atr14;
  const mfiArr = cache.mfi14;
  const adxArr = cache.adx_14;

  if (!cciFastArr || !cciMidArr || !cciSlowArr || !emaTrendArr || !emaTrailArr) return null;

  // Align indices
  const maxPeriod = Math.max(cciFast, cciMid, cciSlow, emaTrend, emaTrail, 14, 14 * 3);
  const startBar = maxPeriod + 10;

  let balance = 100000;
  let position = null; // { side, entry, sl, tp, atr, trailingActive, trailingSL }
  let trades = [];
  let barsSinceExit = 999;
  let peakBalance = balance;
  let maxDD = 0;

  for (let i = startBar; i < candles.length; i++) {
    const c = candles[i];
    const price = c.close;

    // Get indicator values (align to bar i)
    const fIdx = i - (candles.length - cciFastArr.length);
    const mIdx = i - (candles.length - cciMidArr.length);
    const sIdx = i - (candles.length - cciSlowArr.length);
    const eIdx = i - (candles.length - emaTrendArr.length);
    const tIdx = i - (candles.length - emaTrailArr.length);
    const aIdx = i - (candles.length - atrArr.length);
    const mfiIdx = i - (candles.length - mfiArr.length);
    const adxIdx = i - (candles.length - adxArr.length);

    if (fIdx < 1 || mIdx < 0 || sIdx < 0 || eIdx < 0 || tIdx < 0 || aIdx < 0 || mfiIdx < 0) continue;

    const cciFastVal = cciFastArr[fIdx];
    const cciFastPrev = cciFastArr[fIdx - 1];
    const cciMidVal = cciMidArr[mIdx];
    const cciSlowVal = cciSlowArr[sIdx];
    const emaVal = emaTrendArr[eIdx];
    const emaTrailVal = emaTrailArr[tIdx];
    const atr = atrArr[aIdx];
    const mfi = mfiArr[mfiIdx];
    const adx = adxIdx >= 0 && adxArr[adxIdx] ? adxArr[adxIdx].adx : 30;

    // ADX filter
    const adxOK = !useADX || adx >= adxMin;
    const atrOK = atr >= 5;

    // Position management
    if (position) {
      barsSinceExit = 0;
      const isLong = position.side === 'long';

      // Check SL
      if (isLong && c.low <= position.sl) {
        const pnl = (position.sl - position.entry) * 1;
        balance += pnl;
        trades.push({ pnl, side: 'long', exit: 'SL' });
        position = null;
        barsSinceExit = 0;
        continue;
      }
      if (!isLong && c.high >= position.sl) {
        const pnl = (position.entry - position.sl) * 1;
        balance += pnl;
        trades.push({ pnl, side: 'short', exit: 'SL' });
        position = null;
        barsSinceExit = 0;
        continue;
      }

      // Trailing TP
      if (isLong) {
        const floatPnl = price - position.entry;
        if (!position.trailingActive && floatPnl >= position.atr * atrTP) {
          position.trailingActive = true;
          position.trailingSL = emaTrailVal;
        }
        if (position.trailingActive) {
          position.trailingSL = Math.max(position.trailingSL, emaTrailVal);
          if (c.low <= position.trailingSL) {
            const pnl = (position.trailingSL - position.entry) * 1;
            balance += pnl;
            trades.push({ pnl, side: 'long', exit: 'Trail' });
            position = null;
            barsSinceExit = 0;
            continue;
          }
        }
      } else {
        const floatPnl = position.entry - price;
        if (!position.trailingActive && floatPnl >= position.atr * atrTP) {
          position.trailingActive = true;
          position.trailingSL = emaTrailVal;
        }
        if (position.trailingActive) {
          position.trailingSL = Math.min(position.trailingSL, emaTrailVal);
          if (c.high >= position.trailingSL) {
            const pnl = (position.entry - position.trailingSL) * 1;
            balance += pnl;
            trades.push({ pnl, side: 'short', exit: 'Trail' });
            position = null;
            barsSinceExit = 0;
            continue;
          }
        }
      }

      // CCI exit signal
      if (isLong && cciFastVal < 0 && cciFastPrev >= 0) {
        const pnl = (price - position.entry) * 1;
        balance += pnl;
        trades.push({ pnl, side: 'long', exit: 'CCI' });
        position = null;
        barsSinceExit = 0;
        continue;
      }
      if (!isLong && cciFastVal > 0 && cciFastPrev <= 0) {
        const pnl = (position.entry - price) * 1;
        balance += pnl;
        trades.push({ pnl, side: 'short', exit: 'CCI' });
        position = null;
        barsSinceExit = 0;
        continue;
      }

    } else {
      // No position
      barsSinceExit++;
      const cooldownOK = barsSinceExit >= cooldown;

      if (!cooldownOK || !atrOK || !adxOK) continue;

      // Long scoring
      const longCCI1 = cciFastPrev <= 0 && cciFastVal > 0 && cciFastVal > cciThreshold;
      const longCCI2 = cciMidVal > 0;
      const longCCI3 = cciSlowVal > cciSlowTh;
      const longTrend = price > emaVal;
      const longMFI = mfi > 40;
      const longScore = (longCCI1?1:0) + (longCCI2?1:0) + (longCCI3?1:0) + (longTrend?1:0) + (longMFI?1:0);

      if (longScore >= minScore && longCCI1) {
        position = {
          side: 'long', entry: price,
          sl: price - atr * atrSL,
          atr, trailingActive: false, trailingSL: 0
        };
        barsSinceExit = 0;
        continue;
      }

      // Short scoring
      const shortCCI1 = cciFastPrev >= 0 && cciFastVal < 0 && cciFastVal < -cciThreshold;
      const shortCCI2 = cciMidVal < 0;
      const shortCCI3 = cciSlowVal < -cciSlowTh;
      const shortTrend = price < emaVal;
      const shortMFI = mfi < 60;
      const shortScore = (shortCCI1?1:0) + (shortCCI2?1:0) + (shortCCI3?1:0) + (shortTrend?1:0) + (shortMFI?1:0);

      if (shortScore >= minScore && shortCCI1) {
        position = {
          side: 'short', entry: price,
          sl: price + atr * atrSL,
          atr, trailingActive: false, trailingSL: Infinity
        };
        barsSinceExit = 0;
      }
    }

    // Track drawdown
    if (balance > peakBalance) peakBalance = balance;
    const dd = (peakBalance - balance) / peakBalance;
    if (dd > maxDD) maxDD = dd;
  }

  // Close open position
  if (position) {
    const lastPrice = candles[candles.length - 1].close;
    const pnl = position.side === 'long'
      ? (lastPrice - position.entry)
      : (position.entry - lastPrice);
    balance += pnl;
    trades.push({ pnl, side: position.side, exit: 'EOD' });
  }

  const wins = trades.filter(t => t.pnl > 0);
  const losses = trades.filter(t => t.pnl <= 0);
  const grossProfit = wins.reduce((s, t) => s + t.pnl, 0);
  const grossLoss = Math.abs(losses.reduce((s, t) => s + t.pnl, 0));

  return {
    trades: trades.length,
    wins: wins.length,
    winRate: trades.length > 0 ? (wins.length / trades.length * 100) : 0,
    pnl: balance - 100000,
    returnPct: (balance - 100000) / 100000 * 100,
    pf: grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? 99 : 0,
    maxDD: maxDD * 100,
    avgWin: wins.length > 0 ? grossProfit / wins.length : 0,
    avgLoss: losses.length > 0 ? grossLoss / losses.length : 0,
  };
}

// ─── Main ───
const candles = loadCSV(path.join(__dirname, 'COMEX_GC1!, 60.csv'));
console.log(`Loaded ${candles.length} candles`);

const cache = preCompute(candles);
console.log('Indicators pre-computed\n');

// Parameter grid
const grid = {
  cciFast:      [14],
  cciMid:       [25, 30],
  cciSlow:      [60, 80, 100],
  cciThreshold: [20, 50],
  cciSlowTh:    [0, 30, 50],
  emaTrend:     [50],
  emaTrail:     [20, 30, 40],
  atrSL:        [1.5, 1.8, 2.0, 2.5],
  atrTP:        [2.0, 2.5, 2.8, 3.5],
  minScore:     [4, 5],
  cooldown:     [2, 3],
  adxMin:       [0, 15, 20, 25],  // 0 = no ADX
  useADX:       [true],
};

// Generate combinations
let combos = [{}];
for (const [key, values] of Object.entries(grid)) {
  const newCombos = [];
  for (const combo of combos) {
    for (const val of values) {
      newCombos.push({ ...combo, [key]: val });
    }
  }
  combos = newCombos;
}

// When adxMin = 0, useADX = false
combos = combos.map(c => c.adxMin === 0 ? { ...c, useADX: false } : c);

console.log(`Testing ${combos.length} parameter combinations...\n`);

const results = [];
let tested = 0;

for (const params of combos) {
  const r = backtest(candles, cache, params);
  if (r && r.trades >= 30) {
    // Score: weighted combination (return focused)
    const score = r.returnPct * 0.5 + r.pf * 10 + r.winRate * 0.3 - r.maxDD * 0.3;
    results.push({ ...params, ...r, score });
  }
  tested++;
  if (tested % 1000 === 0) process.stdout.write(`  ${tested}/${combos.length}\r`);
}

console.log(`\nCompleted. ${results.length} valid results (≥30 trades)\n`);

// Sort by score
results.sort((a, b) => b.score - a.score);

// Top 15
console.log('═══════════════════════════════════════════════════════════════');
console.log('  TOP 15 PARAMETER SETS (Weighted: Return + PF + WinRate - MDD)');
console.log('═══════════════════════════════════════════════════════════════\n');

const top = results.slice(0, 15);
for (let i = 0; i < top.length; i++) {
  const r = top[i];
  console.log(`#${i + 1} Score: ${r.score.toFixed(1)}`);
  console.log(`  CCI: ${r.cciFast}/${r.cciMid}/${r.cciSlow}  Threshold: ${r.cciThreshold}  SlowTh: ${r.cciSlowTh}`);
  console.log(`  SL: ${r.atrSL}  TP: ${r.atrTP}  Trail EMA: ${r.emaTrail}  Score≥${r.minScore}  CD: ${r.cooldown}`);
  console.log(`  ADX Min: ${r.adxMin}${r.useADX ? '' : ' (OFF)'}`);
  console.log(`  → Return: ${r.returnPct.toFixed(1)}% | PF: ${r.pf.toFixed(2)} | WR: ${r.winRate.toFixed(1)}% | Trades: ${r.trades} | MDD: ${r.maxDD.toFixed(1)}%`);
  console.log(`  → Avg Win: ${r.avgWin.toFixed(1)} | Avg Loss: ${r.avgLoss.toFixed(1)} | W/L Ratio: ${r.avgLoss > 0 ? (r.avgWin / r.avgLoss).toFixed(2) : 'N/A'}`);
  console.log('');
}

// Best by win rate
console.log('═══ BEST BY WIN RATE (≥50 trades) ═══');
const byWR = results.filter(r => r.trades >= 50).sort((a, b) => b.winRate - a.winRate).slice(0, 5);
for (const r of byWR) {
  console.log(`  WR: ${r.winRate.toFixed(1)}% | Ret: ${r.returnPct.toFixed(1)}% | PF: ${r.pf.toFixed(2)} | Trades: ${r.trades} | ADX: ${r.adxMin} | SL: ${r.atrSL} TP: ${r.atrTP} Score≥${r.minScore}`);
}

console.log('\n═══ BEST BY RETURN ═══');
const byRet = [...results].sort((a, b) => b.returnPct - a.returnPct).slice(0, 5);
for (const r of byRet) {
  console.log(`  Ret: ${r.returnPct.toFixed(1)}% | WR: ${r.winRate.toFixed(1)}% | PF: ${r.pf.toFixed(2)} | Trades: ${r.trades} | MDD: ${r.maxDD.toFixed(1)}% | ADX: ${r.adxMin} SL: ${r.atrSL} TP: ${r.atrTP}`);
}

console.log('\n═══ BEST BY PROFIT FACTOR ═══');
const byPF = results.filter(r => r.trades >= 50).sort((a, b) => b.pf - a.pf).slice(0, 5);
for (const r of byPF) {
  console.log(`  PF: ${r.pf.toFixed(2)} | Ret: ${r.returnPct.toFixed(1)}% | WR: ${r.winRate.toFixed(1)}% | Trades: ${r.trades} | ADX: ${r.adxMin} | SL: ${r.atrSL} TP: ${r.atrTP} Score≥${r.minScore}`);
}
