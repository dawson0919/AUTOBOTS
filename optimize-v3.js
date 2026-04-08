/**
 * optimize-v3.js — Triple CCI 高勝率最佳化
 *
 * 改進方向：
 *   1. 進場門檻提高（score >= 4 才進場，不接受 WEAK）
 *   2. CCI 動量過濾（CCI Fast 不只穿 0，要穿過一定幅度）
 *   3. 增加趨勢一致性檢查（3 條 CCI 同向）
 *   4. 評分權重偏向勝率
 *   5. 對比 "高勝率" vs "高報酬" 兩種模式
 */

const fs = require('fs');
const path = require('path');
const { CCI, EMA } = require('./cryptobot/node_modules/technicalindicators');

const CSV_PATH = path.join(__dirname, 'COMEX_GC1!, 60.csv');
const INITIAL_BALANCE = 100000;
const RISK_PER_TRADE = 0.02;
const COMMISSION = 0.0002;
const SLIPPAGE = 0.5;

function loadCSV(fp) {
  return fs.readFileSync(fp, 'utf8').trim().split('\n').slice(1).map(l => {
    const c = l.split(',');
    return { time: +c[0], open: +c[1], high: +c[2], low: +c[3], close: +c[4] };
  });
}

function preCalcATR(candles, period = 14) {
  const arr = new Float64Array(candles.length);
  let atr = 0;
  for (let i = 1; i < candles.length; i++) {
    const tr = Math.max(candles[i].high - candles[i].low, Math.abs(candles[i].high - candles[i-1].close), Math.abs(candles[i].low - candles[i-1].close));
    if (i <= period) { atr += tr; if (i === period) atr /= period; }
    else atr = (atr * (period - 1) + tr) / period;
    if (i >= period) arr[i] = atr;
  }
  return arr;
}

function preCalcMFI(candles, period) {
  const mfi = new Float64Array(candles.length);
  for (let i = period; i < candles.length; i++) {
    let pos = 0, neg = 0;
    for (let j = i - period + 1; j <= i; j++) {
      const r = candles[j].high - candles[j].low;
      if (r === 0) continue;
      const d = (candles[j].close - candles[j].open) / r;
      if (d > 0) pos += d * r; else neg += Math.abs(d) * r;
    }
    mfi[i] = (pos + neg) > 0 ? (pos / (pos + neg)) * 100 : 50;
  }
  return mfi;
}

function preComputeAll(candles) {
  const closes = candles.map(c => c.close), highs = candles.map(c => c.high), lows = candles.map(c => c.low);
  const n = candles.length;
  const cciPeriods = [8, 10, 14, 18, 20, 25, 30, 35, 40, 50, 60, 70, 80];
  const cciCache = {};
  for (const p of cciPeriods) {
    const raw = CCI.calculate({ high: highs, low: lows, close: closes, period: p });
    const arr = new Float64Array(n).fill(NaN); const off = n - raw.length;
    for (let i = 0; i < raw.length; i++) arr[off + i] = raw[i];
    cciCache[p] = arr;
  }
  const emaPeriods = [10, 15, 20, 25, 30, 40, 50, 60, 70, 80];
  const emaCache = {};
  for (const p of emaPeriods) {
    const raw = EMA.calculate({ period: p, values: closes });
    const arr = new Float64Array(n).fill(NaN); const off = n - raw.length;
    for (let i = 0; i < raw.length; i++) arr[off + i] = raw[i];
    emaCache[p] = arr;
  }
  return { cciCache, emaCache, atr: preCalcATR(candles), mfi: preCalcMFI(candles, 14), closes, highs, lows };
}

/**
 * 回測引擎 — 支援多種進場模式
 * mode:
 *   'original'   — 原版（score >= 3 進場）
 *   'strict'     — 嚴格（score >= 4 且需 CCI 動量）
 *   'ultra'      — 超嚴格（score = 5 全條件滿足）
 *   'aligned'    — 三 CCI 同向 + score >= 4
 */
function backtest(candles, ind, p, mode = 'strict') {
  const cciFArr = ind.cciCache[p.cciFast], cciMArr = ind.cciCache[p.cciMid], cciSArr = ind.cciCache[p.cciSlow];
  const emaTArr = ind.emaCache[p.emaTrend], emaRArr = ind.emaCache[p.emaTrail];
  if (!cciFArr || !cciMArr || !cciSArr || !emaTArr || !emaRArr) return null;

  const cciThreshold = p.cciThreshold || 0;  // CCI 動量門檻

  let balance = INITIAL_BALANCE, peakEq = INITIAL_BALANCE, maxDD = 0;
  let pos = null;
  const trades = [];

  for (let i = 80; i < candles.length; i++) {
    const price = ind.closes[i], hi = ind.highs[i], lo = ind.lows[i];
    const ccf = cciFArr[i], ccfP = cciFArr[i-1], ccfP2 = cciFArr[i-2];
    const ccm = cciMArr[i], ccmP = cciMArr[i-1];
    const ccs = cciSArr[i], ccsP = cciSArr[i-1];
    const emaT = emaTArr[i], emaR = emaRArr[i];
    const mfiV = ind.mfi[i], atrV = ind.atr[i];
    if (isNaN(ccf) || isNaN(ccfP) || isNaN(ccfP2) || isNaN(ccm) || isNaN(ccs) || isNaN(emaT) || isNaN(emaR) || atrV <= 0) continue;

    // ── 持倉管理 ──
    if (pos) {
      const isL = pos.side === 1;
      if (pos.trailing) {
        if (isL && emaR > pos.trSL) pos.trSL = emaR;
        if (!isL && emaR < pos.trSL) pos.trSL = emaR;
      } else {
        const fp = isL ? price - pos.entry : pos.entry - price;
        if (fp >= atrV * p.tpMult) { pos.trailing = true; pos.trSL = emaR; }
      }
      let closed = false;
      if (pos.trailing) {
        if ((isL && lo <= pos.trSL) || (!isL && hi >= pos.trSL)) { doClose(pos.trSL, 'TR'); closed = true; }
      }
      if (!closed) {
        if ((isL && lo <= pos.sl) || (!isL && hi >= pos.sl)) { doClose(pos.sl, 'SL'); closed = true; }
      }
      if (!closed && pos) {
        if ((isL && ccf < 0 && ccfP >= 0) || (!isL && ccf > 0 && ccfP <= 0)) { doClose(price, 'EX'); closed = true; }
      }
    }

    // ── 進場邏輯 ──
    if (!pos) {
      let side = 0;

      // 基本條件
      const longCross  = ccfP <= 0 && ccf > 0;
      const shortCross = ccfP >= 0 && ccf < 0;

      if (mode === 'original') {
        // 原版：score >= 3
        const lc2 = ccm > 0, lc3 = ccs > -100, lt = price > emaT, lm = mfiV > 40;
        const sc2 = ccm < 0, sc3 = ccs < 100, st = price < emaT, sm = mfiV < 60;
        const ls = (longCross?1:0)+(lc2?1:0)+(lc3?1:0)+(lt?1:0)+(lm?1:0);
        const ss = (shortCross?1:0)+(sc2?1:0)+(sc3?1:0)+(st?1:0)+(sm?1:0);
        if (ls >= 3 && longCross) side = 1;
        else if (ss >= 3 && shortCross) side = -1;

      } else if (mode === 'strict') {
        // 嚴格：score >= 4 + CCI 動量門檻
        const longMom  = ccf > cciThreshold;     // CCI 不只穿 0，要有動量
        const shortMom = ccf < -cciThreshold;

        const lc2 = ccm > 0, lc3 = ccs > -100, lt = price > emaT, lm = mfiV > 40;
        const sc2 = ccm < 0, sc3 = ccs < 100, st = price < emaT, sm = mfiV < 60;
        const ls = (longCross && longMom?1:0)+(lc2?1:0)+(lc3?1:0)+(lt?1:0)+(lm?1:0);
        const ss = (shortCross && shortMom?1:0)+(sc2?1:0)+(sc3?1:0)+(st?1:0)+(sm?1:0);
        if (ls >= 4 && longCross) side = 1;
        else if (ss >= 4 && shortCross) side = -1;

      } else if (mode === 'ultra') {
        // 超嚴格：5/5 + CCI 動量 + 三 CCI 同向
        const longMom  = ccf > cciThreshold;
        const shortMom = ccf < -cciThreshold;
        const lc2 = ccm > 0, lc3 = ccs > -100, lt = price > emaT, lm = mfiV > 40;
        const sc2 = ccm < 0, sc3 = ccs < 100, st = price < emaT, sm = mfiV < 60;
        // 三 CCI 同向
        const allBull = ccf > 0 && ccm > 0 && ccs > 0;
        const allBear = ccf < 0 && ccm < 0 && ccs < 0;
        const ls = (longCross && longMom?1:0)+(lc2?1:0)+(lc3?1:0)+(lt?1:0)+(lm?1:0);
        const ss = (shortCross && shortMom?1:0)+(sc2?1:0)+(sc3?1:0)+(st?1:0)+(sm?1:0);
        if (ls === 5 && longCross && allBull) side = 1;
        else if (ss === 5 && shortCross && allBear) side = -1;

      } else if (mode === 'aligned') {
        // 三 CCI 同向 + score >= 4
        const longMom  = ccf > cciThreshold;
        const shortMom = ccf < -cciThreshold;
        const lc2 = ccm > 0, lc3 = ccs > 0, lt = price > emaT, lm = mfiV > 40;  // ccs > 0 而非 > -100
        const sc2 = ccm < 0, sc3 = ccs < 0, st = price < emaT, sm = mfiV < 60;
        const ls = (longCross && longMom?1:0)+(lc2?1:0)+(lc3?1:0)+(lt?1:0)+(lm?1:0);
        const ss = (shortCross && shortMom?1:0)+(sc2?1:0)+(sc3?1:0)+(st?1:0)+(sm?1:0);
        if (ls >= 4 && longCross) side = 1;
        else if (ss >= 4 && shortCross) side = -1;
      }

      if (side && atrV > 0) {
        const isL = side === 1, entry = price + (isL ? SLIPPAGE : -SLIPPAGE);
        const sl = isL ? entry - atrV * p.slMult : entry + atrV * p.slMult;
        const d = Math.abs(entry - sl);
        if (d > 0) {
          const qty = (balance * RISK_PER_TRADE) / d;
          if (qty * entry <= balance * 20) {
            balance -= qty * entry * COMMISSION;
            pos = { side, entry, qty, sl, trailing: false, trSL: 0 };
          }
        }
      }
    }

    const ur = pos ? (pos.side === 1 ? price - pos.entry : pos.entry - price) * pos.qty : 0;
    const eq = balance + ur;
    if (eq > peakEq) peakEq = eq;
    const dd = (peakEq - eq) / peakEq;
    if (dd > maxDD) maxDD = dd;
  }
  if (pos) doClose(ind.closes[candles.length - 1], 'END');

  function doClose(ep, reason) {
    if (!pos) return;
    const slip = pos.side === 1 ? -SLIPPAGE : SLIPPAGE;
    const act = ep + slip;
    const pnl = pos.side === 1 ? (act - pos.entry) * pos.qty : (pos.entry - act) * pos.qty;
    const net = pnl - pos.qty * act * COMMISSION;
    balance += net;
    trades.push({ pnl: net, ret: pos.side === 1 ? (act-pos.entry)/pos.entry*100 : (pos.entry-act)/pos.entry*100, reason });
    pos = null;
  }

  if (trades.length < 3) return null;
  const wins = trades.filter(t => t.pnl > 0), losses = trades.filter(t => t.pnl <= 0);
  const gp = wins.reduce((s,t) => s+t.pnl, 0), gl = Math.abs(losses.reduce((s,t) => s+t.pnl, 0));
  const rets = trades.map(t => t.ret);
  const avgR = rets.reduce((s,r) => s+r, 0) / rets.length;
  const stdR = Math.sqrt(rets.reduce((s,r) => s+(r-avgR)**2, 0) / rets.length);
  return {
    total: trades.length, wins: wins.length, losses: losses.length,
    wr: wins.length / trades.length * 100,
    totalRet: (balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100,
    totalPnl: trades.reduce((s,t) => s+t.pnl, 0),
    pf: gl > 0 ? gp / gl : 99,
    sharpe: stdR > 0 ? avgR / stdR : 0,
    mdd: maxDD * 100,
    avgW: wins.length > 0 ? gp / wins.length : 0,
    avgL: losses.length > 0 ? gl / losses.length : 0,
    trailCount: trades.filter(t => t.reason === 'TR').length,
    slCount: trades.filter(t => t.reason === 'SL').length,
    balance,
  };
}

// 勝率導向評分
function scoreWR(s) {
  if (!s) return -Infinity;
  return Math.min(s.pf, 3) / 3 * 0.20
       + Math.min(Math.max(s.totalRet, -50), 100) / 100 * 0.15
       + Math.min(Math.max(s.sharpe, 0), 1) * 0.15
       + Math.max(0, (50 - s.mdd)) / 50 * 0.15
       + s.wr / 100 * 0.35;   // 勝率權重 35%
}

function printResults(label, results, n = 8) {
  console.log(`\n  ${label}:`);
  console.log(`    ${'#'.padStart(3)} | ${'CCI'.padEnd(12)} | ${'SL'.padStart(4)} ${'TP'.padStart(4)} ${'EMA'.padStart(4)} ${'Tr'.padStart(3)} ${'CCIth'.padStart(5)} | ` +
    `${'報酬%'.padStart(8)} | ${'勝率%'.padStart(6)} | ${'PF'.padStart(5)} | ${'MDD%'.padStart(6)} | ${'筆數'.padStart(4)} | ${'SL次'.padStart(4)} | ${'評分'.padStart(7)}`);
  console.log('    ' + '-'.repeat(100));
  for (let i = 0; i < Math.min(n, results.length); i++) {
    const { params: p, stats: s, score: sc } = results[i];
    console.log(
      `    ${String(i+1).padStart(3)} | ${`${p.cciFast}/${p.cciMid}/${p.cciSlow}`.padEnd(12)} | ` +
      `${p.slMult.toFixed(1).padStart(4)} ${p.tpMult.toFixed(1).padStart(4)} ${String(p.emaTrend).padStart(4)} ${String(p.emaTrail).padStart(3)} ${String(p.cciThreshold||0).padStart(5)} | ` +
      `${(s.totalRet>=0?'+':'')+s.totalRet.toFixed(1)+'%'}`.padStart(8) + ` | ${s.wr.toFixed(1).padStart(5)}% | ` +
      `${s.pf.toFixed(2).padStart(5)} | ${s.mdd.toFixed(1).padStart(5)}% | ${String(s.total).padStart(4)} | ${String(s.slCount).padStart(4)} | ${sc.toFixed(4).padStart(7)}`
    );
  }
}

function main() {
  console.log('📦 載入資料...');
  const candles = loadCSV(CSV_PATH);
  const ind = preComputeAll(candles);
  console.log(`✅ ${candles.length} 根 K 棒，指標預計算完成\n`);

  // ═════════════════════════════════════════
  //  對比 4 種進場模式（使用上一輪最佳參數）
  // ═════════════════════════════════════════
  console.log('═'.repeat(80));
  console.log('  🔬 Step 1: 進場模式對比（固定參數，改變進場嚴格度）');
  console.log('═'.repeat(80));

  const baseP = { cciFast: 14, cciMid: 30, cciSlow: 80, slMult: 1.5, tpMult: 2.75, emaTrend: 50, emaTrail: 40, cciThreshold: 0 };
  const modes = ['original', 'strict', 'aligned', 'ultra'];
  const modeLabels = { original: '原版(≥3)', strict: '嚴格(≥4)', aligned: '同向(≥4)', ultra: '超嚴格(=5)' };

  console.log(`\n  ${'模式'.padEnd(14)} | ${'報酬%'.padStart(8)} | ${'勝率%'.padStart(6)} | ${'PF'.padStart(5)} | ${'MDD%'.padStart(6)} | ${'筆數'.padStart(4)} | ${'SL次'.padStart(4)} | ${'追蹤'.padStart(4)}`);
  console.log('  ' + '-'.repeat(70));
  for (const m of modes) {
    const s = backtest(candles, ind, baseP, m);
    if (!s) { console.log(`  ${modeLabels[m].padEnd(14)} | 交易不足`); continue; }
    console.log(
      `  ${modeLabels[m].padEnd(14)} | ${(s.totalRet>=0?'+':'')+s.totalRet.toFixed(1)+'%'}`.padEnd(26) +
      `| ${s.wr.toFixed(1).padStart(5)}% | ${s.pf.toFixed(2).padStart(5)} | ${s.mdd.toFixed(1).padStart(5)}% | ${String(s.total).padStart(4)} | ${String(s.slCount).padStart(4)} | ${String(s.trailCount).padStart(4)}`
    );
  }

  // ═════════════════════════════════════════
  //  Step 2: 最佳進場模式 + CCI 動量門檻搜尋
  // ═════════════════════════════════════════
  console.log('\n' + '═'.repeat(80));
  console.log('  🔬 Step 2: 各模式 + CCI 動量門檻 + SL/TP 聯合搜尋');
  console.log('═'.repeat(80));

  const bestByMode = {};

  for (const mode of ['strict', 'aligned', 'ultra']) {
    const grid = [];
    const cciThresholds = [0, 20, 50, 80, 100, 120, 150];
    const slRange = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5];
    const tpRange = [1.5, 2.0, 2.5, 2.75, 3.0, 3.5];
    const trailRange = [15, 20, 30, 40];

    for (const cciThreshold of cciThresholds)
      for (const slMult of slRange)
        for (const tpMult of tpRange) { if (tpMult <= slMult) continue;
          for (const emaTrail of trailRange) {
            grid.push({ cciFast: 14, cciMid: 30, cciSlow: 80, slMult, tpMult, emaTrend: 50, emaTrail, cciThreshold });
          }
        }

    const results = [];
    for (const p of grid) {
      const s = backtest(candles, ind, p, mode);
      if (s && s.total >= 10) results.push({ params: p, stats: s, score: scoreWR(s) });
    }
    results.sort((a, b) => b.score - a.score);
    bestByMode[mode] = results;

    console.log(`\n  📦 ${modeLabels[mode]}: ${grid.length} 組合, ${results.length} 有效 (≥10筆)`);
    printResults(`${modeLabels[mode]} Top 8 (勝率導向評分)`, results, 8);
  }

  // ═════════════════════════════════════════
  //  Step 3: 精搜最佳模式
  // ═════════════════════════════════════════
  // 找出勝率最高的模式
  const allBest = Object.entries(bestByMode)
    .filter(([_, r]) => r.length > 0)
    .map(([mode, r]) => ({ mode, ...r[0] }))
    .sort((a, b) => b.score - a.score);

  const winnerMode = allBest[0]?.mode || 'strict';
  const winnerP = allBest[0]?.params || baseP;

  console.log('\n' + '═'.repeat(80));
  console.log(`  🎯 Step 3: 精搜 [${modeLabels[winnerMode]}] 模式`);
  console.log(`  基準: CCI th=${winnerP.cciThreshold} SL=${winnerP.slMult} TP=${winnerP.tpMult} Trail=${winnerP.emaTrail}`);
  console.log('═'.repeat(80));

  // 在最佳值附近微調
  const fineGrid = [];
  const thNear = [...new Set([winnerP.cciThreshold - 20, winnerP.cciThreshold - 10, winnerP.cciThreshold, winnerP.cciThreshold + 10, winnerP.cciThreshold + 20].filter(v => v >= 0 && v <= 200))];
  const slNear = [...new Set([winnerP.slMult - 0.25, winnerP.slMult, winnerP.slMult + 0.25].filter(v => v >= 0.75 && v <= 3.0))];
  const tpNear = [...new Set([winnerP.tpMult - 0.25, winnerP.tpMult, winnerP.tpMult + 0.25].filter(v => v >= 1.5 && v <= 4.0))];
  const trNear = [...new Set([winnerP.emaTrail - 5, winnerP.emaTrail, winnerP.emaTrail + 5].filter(v => v >= 10 && v <= 50))];
  const cciMidNear = [25, 30, 35];
  const cciSlowNear = [60, 70, 80];

  for (const cciMid of cciMidNear)
    for (const cciSlow of cciSlowNear) { if (cciSlow <= cciMid) continue;
      for (const cciThreshold of thNear)
        for (const slMult of slNear)
          for (const tpMult of tpNear) { if (tpMult <= slMult) continue;
            for (const emaTrail of trNear) {
              fineGrid.push({ cciFast: 14, cciMid, cciSlow, slMult, tpMult, emaTrend: 50, emaTrail, cciThreshold });
            }
          }
    }

  const fineResults = [];
  for (const p of fineGrid) {
    const s = backtest(candles, ind, p, winnerMode);
    if (s && s.total >= 10) fineResults.push({ params: p, stats: s, score: scoreWR(s) });
  }
  fineResults.sort((a, b) => b.score - a.score);

  console.log(`\n  📦 精搜: ${fineGrid.length} 組合, ${fineResults.length} 有效`);
  printResults('精搜 Top 10 (勝率導向)', fineResults, 10);

  // ═════════════════════════════════════════
  //  最終報告
  // ═════════════════════════════════════════
  const finalBest = fineResults[0] || allBest[0];
  const fb = finalBest.stats;
  const fp = finalBest.params;

  // 用原版參數跑原版模式作為基準
  const origStats = backtest(candles, ind, { cciFast:14,cciMid:25,cciSlow:50,slMult:1.75,tpMult:2.25,emaTrend:50,emaTrail:20,cciThreshold:0 }, 'original');
  // 用 v2 最佳參數跑原版模式
  const v2Stats = backtest(candles, ind, { cciFast:14,cciMid:30,cciSlow:80,slMult:1.5,tpMult:2.75,emaTrend:50,emaTrail:40,cciThreshold:0 }, 'original');

  console.log('\n' + '═'.repeat(80));
  console.log('  🏆 三版本對比');
  console.log('═'.repeat(80));

  console.log(`\n  V1 預設:  CCI(14/25/50) SL=1.75 TP=2.25 Trail=20 | 原版模式`);
  console.log(`  V2 高報酬: CCI(14/30/80) SL=1.5 TP=2.75 Trail=40  | 原版模式`);
  console.log(`  V3 高勝率: CCI(${fp.cciFast}/${fp.cciMid}/${fp.cciSlow}) SL=${fp.slMult} TP=${fp.tpMult} Trail=${fp.emaTrail} CCIth=${fp.cciThreshold} | ${modeLabels[winnerMode]}`);

  const os = origStats, vs = v2Stats;
  console.log(`\n  ${'指標'.padEnd(12)} | ${'V1 預設'.padStart(14)} | ${'V2 高報酬'.padStart(14)} | ${'V3 高勝率'.padStart(14)}`);
  console.log('  ' + '-'.repeat(62));
  const rows = [
    ['總報酬率', `${os.totalRet.toFixed(1)}%`, `${vs.totalRet.toFixed(1)}%`, `${fb.totalRet.toFixed(1)}%`],
    ['勝率',     `${os.wr.toFixed(1)}%`,       `${vs.wr.toFixed(1)}%`,       `${fb.wr.toFixed(1)}%`],
    ['盈虧比(PF)', `${os.pf.toFixed(2)}`,       `${vs.pf.toFixed(2)}`,       `${fb.pf.toFixed(2)}`],
    ['Sharpe',   `${os.sharpe.toFixed(3)}`,     `${vs.sharpe.toFixed(3)}`,   `${fb.sharpe.toFixed(3)}`],
    ['最大回撤',  `${os.mdd.toFixed(1)}%`,      `${vs.mdd.toFixed(1)}%`,     `${fb.mdd.toFixed(1)}%`],
    ['交易次數',  `${os.total}`,                 `${vs.total}`,               `${fb.total}`],
    ['止損次數',  `${os.slCount}`,               `${vs.slCount}`,             `${fb.slCount}`],
    ['追蹤止盈',  `${os.trailCount}`,            `${vs.trailCount}`,          `${fb.trailCount}`],
  ];
  for (const [l, a, b, c] of rows) console.log(`  ${l.padEnd(12)} | ${a.padStart(14)} | ${b.padStart(14)} | ${c.padStart(14)}`);
  console.log('═'.repeat(80));

  // 穩健性
  const top5 = fineResults.slice(0, 5);
  if (top5.length >= 3) {
    console.log('\n  📐 穩健性（V3 Top 5 收斂）:');
    const pnames = ['cciMid','cciSlow','slMult','tpMult','emaTrail','cciThreshold'];
    const labels = ['CCI Mid','CCI Slow','ATR SL','ATR TP','Trail','CCI門檻'];
    for (let j = 0; j < pnames.length; j++) {
      const vals = top5.map(r => r.params[pnames[j]]);
      const mn = Math.min(...vals), mx = Math.max(...vals);
      const avg = vals.reduce((a,b) => a+b, 0) / vals.length;
      const st = mn===mx ? '✅ 收斂' : (mx-mn <= avg*0.3 ? '🟡 窄幅' : '🔴 分散');
      console.log(`  ${labels[j].padEnd(10)}: ${mn} ~ ${mx} (avg ${avg.toFixed(1)}) ${st}`);
    }
  }

  const out = path.join(__dirname, 'optimize-results-v3.json');
  fs.writeFileSync(out, JSON.stringify({
    modes_comparison: modes.map(m => ({ mode: m, stats: backtest(candles, ind, baseP, m) })),
    winnerMode,
    bestParams: fp, bestStats: fb, bestScore: finalBest.score,
    v1_default: origStats, v2_highReturn: vs,
    top10: fineResults.slice(0,10).map(r => ({ params: r.params, stats: r.stats, score: r.score })),
  }, null, 2));
  console.log(`\n💾 結果已儲存: ${out}`);
}

main();
