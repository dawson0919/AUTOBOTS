/**
 * optimize-v2.js — Triple CCI 分組最佳化
 *
 * 兩階段搜尋：
 *   Phase 1 — 粗網格（3 組，每組 ~2000 組合）分別最佳化 CCI / ATR / EMA
 *   Phase 2 — 精搜（~2000 組合）圍繞 Phase 1 最佳值細調
 *
 * node optimize-v2.js
 */

const fs = require('fs');
const path = require('path');
const { CCI, EMA } = require('./cryptobot/node_modules/technicalindicators');

const CSV_PATH = path.join(__dirname, 'COMEX_GC1!, 60.csv');
const INITIAL_BALANCE = 100000;
const RISK_PER_TRADE = 0.02;
const COMMISSION = 0.0002;
const SLIPPAGE = 0.5;

// ─────────────────────────────────────────────
//  CSV & 指標預計算
// ─────────────────────────────────────────────
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
    const tr = Math.max(candles[i].high - candles[i].low, Math.abs(candles[i].high - candles[i - 1].close), Math.abs(candles[i].low - candles[i - 1].close));
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
      const range = candles[j].high - candles[j].low;
      if (range === 0) continue;
      const dir = (candles[j].close - candles[j].open) / range;
      if (dir > 0) pos += dir * range; else neg += Math.abs(dir) * range;
    }
    mfi[i] = (pos + neg) > 0 ? (pos / (pos + neg)) * 100 : 50;
  }
  return mfi;
}

function preComputeAll(candles) {
  const closes = candles.map(c => c.close);
  const highs = candles.map(c => c.high);
  const lows = candles.map(c => c.low);
  const n = candles.length;

  // CCI: 步長大 → 8, 14, 20, 26, 30, 40, 50, 60, 70, 80
  const cciPeriods = [8, 10, 14, 18, 20, 25, 30, 35, 40, 50, 60, 70, 80];
  const cciCache = {};
  for (const p of cciPeriods) {
    const raw = CCI.calculate({ high: highs, low: lows, close: closes, period: p });
    const arr = new Float64Array(n).fill(NaN);
    const off = n - raw.length;
    for (let i = 0; i < raw.length; i++) arr[off + i] = raw[i];
    cciCache[p] = arr;
  }

  // EMA: 10, 15, 20, 25, 30, 40, 50, 60, 70, 80
  const emaPeriods = [10, 15, 20, 25, 30, 40, 50, 60, 70, 80];
  const emaCache = {};
  for (const p of emaPeriods) {
    const raw = EMA.calculate({ period: p, values: closes });
    const arr = new Float64Array(n).fill(NaN);
    const off = n - raw.length;
    for (let i = 0; i < raw.length; i++) arr[off + i] = raw[i];
    emaCache[p] = arr;
  }

  return { cciCache, emaCache, atr: preCalcATR(candles), mfi: preCalcMFI(candles, 14), closes, highs, lows };
}

// ─────────────────────────────────────────────
//  快速回測
// ─────────────────────────────────────────────
function fastBacktest(candles, ind, p) {
  const cciFArr = ind.cciCache[p.cciFast];
  const cciMArr = ind.cciCache[p.cciMid];
  const cciSArr = ind.cciCache[p.cciSlow];
  const emaTArr = ind.emaCache[p.emaTrend];
  const emaRArr = ind.emaCache[p.emaTrail];
  if (!cciFArr || !cciMArr || !cciSArr || !emaTArr || !emaRArr) return null;

  let balance = INITIAL_BALANCE, peakEq = INITIAL_BALANCE, maxDD = 0;
  let pos = null;
  const trades = [];

  for (let i = 80; i < candles.length; i++) {
    const price = ind.closes[i], hi = ind.highs[i], lo = ind.lows[i];
    const ccf = cciFArr[i], ccfP = cciFArr[i - 1];
    const ccm = cciMArr[i], ccs = cciSArr[i];
    const emaT = emaTArr[i], emaR = emaRArr[i];
    const mfiV = ind.mfi[i], atrV = ind.atr[i];
    if (isNaN(ccf) || isNaN(ccfP) || isNaN(ccm) || isNaN(ccs) || isNaN(emaT) || isNaN(emaR) || atrV <= 0) continue;

    if (pos) {
      const isL = pos.side === 1;
      // 追蹤更新
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

    if (!pos) {
      const lc1 = ccfP <= 0 && ccf > 0, lc2 = ccm > 0, lc3 = ccs > -100, lt = price > emaT, lm = mfiV > 40;
      const sc1 = ccfP >= 0 && ccf < 0, sc2 = ccm < 0, sc3 = ccs < 100, st = price < emaT, sm = mfiV < 60;
      const ls = (lc1?1:0)+(lc2?1:0)+(lc3?1:0)+(lt?1:0)+(lm?1:0);
      const ss = (sc1?1:0)+(sc2?1:0)+(sc3?1:0)+(st?1:0)+(sm?1:0);
      let side = 0;
      if (ls >= 4 && lc1) side = 1; else if (ss >= 4 && sc1) side = -1;
      else if (ls >= 3 && lc1) side = 1; else if (ss >= 3 && sc1) side = -1;
      if (side && atrV > 0) {
        const isL = side === 1;
        const entry = price + (isL ? SLIPPAGE : -SLIPPAGE);
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
    trades.push({ pnl: net, ret: pos.side === 1 ? (act - pos.entry) / pos.entry * 100 : (pos.entry - act) / pos.entry * 100, reason });
    pos = null;
  }

  if (trades.length < 5) return null;
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
    balance,
  };
}

function score(s) {
  if (!s) return -Infinity;
  return Math.min(s.pf, 3) / 3 * 0.30
       + Math.min(Math.max(s.totalRet, -50), 100) / 100 * 0.25
       + Math.min(Math.max(s.sharpe, 0), 1) * 0.20
       + Math.max(0, (50 - s.mdd)) / 50 * 0.15
       + s.wr / 100 * 0.10;
}

function runGroup(name, grid, candles, ind) {
  console.log(`\n  📦 ${name}: ${grid.length} 組合`);
  const results = [];
  const t0 = Date.now();
  for (let i = 0; i < grid.length; i++) {
    const stats = fastBacktest(candles, ind, grid[i]);
    if (stats) results.push({ params: grid[i], stats, score: score(stats) });
    if (Date.now() - t0 > 3000 * Math.floor(i / 3000 + 1) || i === grid.length - 1) {
      process.stdout.write(`\r    ${((i+1)/grid.length*100).toFixed(0)}% (${i+1}/${grid.length}) | ${((Date.now()-t0)/1000).toFixed(1)}s`);
    }
  }
  results.sort((a, b) => b.score - a.score);
  console.log(`  ✅ ${results.length} 有效`);
  return results;
}

function printTop(results, n = 10) {
  console.log(
    `    ${'#'.padStart(3)} | ${'CCI'.padEnd(12)} | ${'SL'.padStart(5)} | ${'TP'.padStart(5)} | ` +
    `${'EMA'.padStart(4)} | ${'Tr'.padStart(3)} | ` +
    `${'報酬%'.padStart(8)} | ${'勝率'.padStart(5)} | ${'PF'.padStart(5)} | ${'MDD%'.padStart(6)} | ${'評分'.padStart(7)}`
  );
  console.log('    ' + '-'.repeat(90));
  for (let i = 0; i < Math.min(n, results.length); i++) {
    const { params: p, stats: s, score: sc } = results[i];
    console.log(
      `    ${String(i+1).padStart(3)} | ${`${p.cciFast}/${p.cciMid}/${p.cciSlow}`.padEnd(12)} | ` +
      `${p.slMult.toFixed(1).padStart(5)} | ${p.tpMult.toFixed(1).padStart(5)} | ` +
      `${String(p.emaTrend).padStart(4)} | ${String(p.emaTrail).padStart(3)} | ` +
      `${(s.totalRet>=0?'+':'')+s.totalRet.toFixed(1)+'%'}`.padStart(8) + ` | ${s.wr.toFixed(1)+'%'.padStart(4)} | ` +
      `${s.pf.toFixed(2).padStart(5)} | ${s.mdd.toFixed(1).padStart(5)}% | ${sc.toFixed(4).padStart(7)}`
    );
  }
}

// ═══════════════════════════════════════════════
//  MAIN
// ═══════════════════════════════════════════════
function main() {
  console.log('📦 載入資料...');
  const candles = loadCSV(CSV_PATH);
  console.log(`✅ ${candles.length} 根 K 棒`);
  const ind = preComputeAll(candles);
  console.log('✅ 指標預計算完成');

  // ═════════════════════════════════════════
  //  Phase 1A — CCI 週期最佳化（固定 SL/TP/EMA）
  // ═════════════════════════════════════════
  console.log('\n' + '═'.repeat(80));
  console.log('  🔬 Phase 1A: CCI 週期最佳化');
  console.log('  固定: SL=1.5, TP=2.5, EMA趨勢=50, Trail=20');
  console.log('═'.repeat(80));

  const gridCCI = [];
  // CCI Fast: 8~20 步長4, Mid: 20~40 步長5, Slow: 40~80 步長10
  for (const cciFast of [8, 10, 14, 18, 20])
    for (const cciMid of [20, 25, 30, 35, 40]) { if (cciMid <= cciFast) continue;
      for (const cciSlow of [40, 50, 60, 70, 80]) { if (cciSlow <= cciMid) continue;
        gridCCI.push({ cciFast, cciMid, cciSlow, slMult: 1.5, tpMult: 2.5, emaTrend: 50, emaTrail: 20 });
      }
    }

  const resCCI = runGroup('CCI 週期搜尋', gridCCI, candles, ind);
  console.log('\n    📊 CCI Top 5:');
  printTop(resCCI, 5);

  const bestCCI = resCCI[0]?.params || { cciFast: 14, cciMid: 30, cciSlow: 70 };

  // ═════════════════════════════════════════
  //  Phase 1B — ATR SL/TP 最佳化（固定最佳 CCI）
  // ═════════════════════════════════════════
  console.log('\n' + '═'.repeat(80));
  console.log(`  🔬 Phase 1B: ATR SL/TP 最佳化`);
  console.log(`  固定 CCI: ${bestCCI.cciFast}/${bestCCI.cciMid}/${bestCCI.cciSlow}`);
  console.log('═'.repeat(80));

  const gridATR = [];
  // SL: 0.75~2.5 步長0.25, TP: 1.5~4.0 步長0.5
  for (const slMult of [0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5])
    for (const tpMult of [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]) { if (tpMult <= slMult) continue;
      for (const emaTrend of [30, 50, 70])
        for (const emaTrail of [10, 20, 30]) { if (emaTrail >= emaTrend) continue;
          gridATR.push({ ...bestCCI, slMult, tpMult, emaTrend, emaTrail });
        }
    }

  const resATR = runGroup('ATR SL/TP 搜尋', gridATR, candles, ind);
  console.log('\n    📊 ATR Top 5:');
  printTop(resATR, 5);

  const bestATR = resATR[0]?.params || { slMult: 1.5, tpMult: 2.5 };

  // ═════════════════════════════════════════
  //  Phase 1C — EMA 趨勢/追蹤 最佳化
  // ═════════════════════════════════════════
  console.log('\n' + '═'.repeat(80));
  console.log(`  🔬 Phase 1C: EMA 趨勢/追蹤 最佳化`);
  console.log(`  固定 CCI: ${bestCCI.cciFast}/${bestCCI.cciMid}/${bestCCI.cciSlow} | SL=${bestATR.slMult} TP=${bestATR.tpMult}`);
  console.log('═'.repeat(80));

  const gridEMA = [];
  for (const emaTrend of [20, 30, 40, 50, 60, 70, 80])
    for (const emaTrail of [10, 15, 20, 25, 30, 40]) { if (emaTrail >= emaTrend) continue;
      gridEMA.push({ cciFast: bestCCI.cciFast, cciMid: bestCCI.cciMid, cciSlow: bestCCI.cciSlow, slMult: bestATR.slMult, tpMult: bestATR.tpMult, emaTrend, emaTrail });
    }

  const resEMA = runGroup('EMA 趨勢/追蹤搜尋', gridEMA, candles, ind);
  console.log('\n    📊 EMA Top 5:');
  printTop(resEMA, 5);

  const bestEMA = resEMA[0]?.params || { emaTrend: 50, emaTrail: 15 };

  // ═════════════════════════════════════════
  //  Phase 2 — 精搜：圍繞最佳值微調
  // ═════════════════════════════════════════
  const base = {
    cciFast: bestCCI.cciFast, cciMid: bestCCI.cciMid, cciSlow: bestCCI.cciSlow,
    slMult: bestATR.slMult, tpMult: bestATR.tpMult,
    emaTrend: bestEMA.emaTrend, emaTrail: bestEMA.emaTrail,
  };

  console.log('\n' + '═'.repeat(80));
  console.log(`  🎯 Phase 2: 精搜微調`);
  console.log(`  基準: CCI(${base.cciFast}/${base.cciMid}/${base.cciSlow}) SL=${base.slMult} TP=${base.tpMult} EMA=${base.emaTrend} Trail=${base.emaTrail}`);
  console.log('═'.repeat(80));

  // 在最佳值附近 ±1~2 步微調
  function nearby(val, candidates) { return candidates.filter(c => c !== undefined); }

  const cciF2 = [...new Set([base.cciFast - 4, base.cciFast - 2, base.cciFast, base.cciFast + 2, base.cciFast + 4].filter(v => v >= 8 && v <= 20))];
  const cciM2 = [...new Set([base.cciMid - 5, base.cciMid, base.cciMid + 5].filter(v => v >= 20 && v <= 50))];
  const cciS2 = [...new Set([base.cciSlow - 10, base.cciSlow, base.cciSlow + 10].filter(v => v >= 40 && v <= 80))];
  const sl2 = [...new Set([base.slMult - 0.25, base.slMult, base.slMult + 0.25].filter(v => v >= 0.75 && v <= 2.5))];
  const tp2 = [...new Set([base.tpMult - 0.5, base.tpMult - 0.25, base.tpMult, base.tpMult + 0.25, base.tpMult + 0.5].filter(v => v >= 1.5 && v <= 4.0))];
  const et2 = [...new Set([base.emaTrend - 10, base.emaTrend, base.emaTrend + 10].filter(v => v >= 20 && v <= 80))];
  const er2 = [...new Set([base.emaTrail - 5, base.emaTrail, base.emaTrail + 5].filter(v => v >= 10 && v <= 40))];

  const gridFine = [];
  for (const cciFast of cciF2)
    for (const cciMid of cciM2) { if (cciMid <= cciFast) continue;
      for (const cciSlow of cciS2) { if (cciSlow <= cciMid) continue;
        for (const slMult of sl2)
          for (const tpMult of tp2) { if (tpMult <= slMult) continue;
            for (const emaTrend of et2)
              for (const emaTrail of er2) { if (emaTrail >= emaTrend) continue;
                gridFine.push({ cciFast, cciMid, cciSlow, slMult, tpMult, emaTrend, emaTrail });
              }
          }
      }
    }

  const resFine = runGroup('精搜微調', gridFine, candles, ind);
  console.log('\n    📊 精搜 Top 10:');
  printTop(resFine, 10);

  // ═════════════════════════════════════════
  //  最終報告
  // ═════════════════════════════════════════
  const best = resFine[0] || resATR[0];
  const dp = { cciFast: 14, cciMid: 25, cciSlow: 50, slMult: 1.75, tpMult: 2.25, emaTrend: 50, emaTrail: 20 };
  const ds = fastBacktest(candles, ind, dp);
  const dsc = score(ds);

  const bp = best.params;
  const bs = best.stats;

  console.log('\n' + '═'.repeat(70));
  console.log('  🏆 最終結果：最佳參數 vs 預設參數');
  console.log('═'.repeat(70));
  console.log(`\n  預設: CCI(14/25/50) SL=1.75 TP=2.25 EMA=50 Trail=20`);
  console.log(`  最佳: CCI(${bp.cciFast}/${bp.cciMid}/${bp.cciSlow}) SL=${bp.slMult} TP=${bp.tpMult} EMA=${bp.emaTrend} Trail=${bp.emaTrail}\n`);

  const rows = [
    ['總報酬率',   `${ds.totalRet.toFixed(1)}%`,  `${bs.totalRet.toFixed(1)}%`,  `${(bs.totalRet-ds.totalRet)>=0?'+':''}${(bs.totalRet-ds.totalRet).toFixed(1)}%`],
    ['總損益',     `$${ds.totalPnl.toFixed(0)}`,   `$${bs.totalPnl.toFixed(0)}`,  `$${(bs.totalPnl-ds.totalPnl)>=0?'+':''}${(bs.totalPnl-ds.totalPnl).toFixed(0)}`],
    ['勝率',       `${ds.wr.toFixed(1)}%`,         `${bs.wr.toFixed(1)}%`,        `${(bs.wr-ds.wr)>=0?'+':''}${(bs.wr-ds.wr).toFixed(1)}%`],
    ['盈虧比(PF)', `${ds.pf.toFixed(2)}`,          `${bs.pf.toFixed(2)}`,         `${(bs.pf-ds.pf)>=0?'+':''}${(bs.pf-ds.pf).toFixed(2)}`],
    ['Sharpe',     `${ds.sharpe.toFixed(3)}`,       `${bs.sharpe.toFixed(3)}`,     `${(bs.sharpe-ds.sharpe)>=0?'+':''}${(bs.sharpe-ds.sharpe).toFixed(3)}`],
    ['最大回撤',   `${ds.mdd.toFixed(1)}%`,        `${bs.mdd.toFixed(1)}%`,       `${(ds.mdd-bs.mdd)>=0?'':'-'}${Math.abs(ds.mdd-bs.mdd).toFixed(1)}% 改善`],
    ['交易次數',   `${ds.total}`,                   `${bs.total}`,                 ``],
    ['追蹤止盈次',  `${ds.trailCount}`,             `${bs.trailCount}`,            ``],
    ['綜合評分',   `${dsc.toFixed(4)}`,             `${best.score.toFixed(4)}`,    `+${(best.score-dsc).toFixed(4)}`],
  ];

  console.log(`  ${'指標'.padEnd(12)} | ${'預設'.padStart(14)} | ${'最佳'.padStart(14)} | ${'改善'.padStart(14)}`);
  console.log('  ' + '-'.repeat(62));
  for (const [l, d, b, diff] of rows) console.log(`  ${l.padEnd(12)} | ${d.padStart(14)} | ${b.padStart(14)} | ${diff.padStart(14)}`);
  console.log('═'.repeat(70));

  // 穩健性
  const top5 = resFine.slice(0, 5);
  console.log('\n  📐 穩健性分析（精搜 Top 5 收斂區間）:');
  const pnames = ['cciFast', 'cciMid', 'cciSlow', 'slMult', 'tpMult', 'emaTrend', 'emaTrail'];
  const labels = ['CCI Fast', 'CCI Mid', 'CCI Slow', 'ATR SL', 'ATR TP', 'EMA 趨勢', 'Trail EMA'];
  for (let j = 0; j < pnames.length; j++) {
    const vals = top5.map(r => r.params[pnames[j]]);
    const mn = Math.min(...vals), mx = Math.max(...vals);
    const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
    const st = mn === mx ? '✅ 收斂' : (mx - mn <= avg * 0.3 ? '🟡 窄幅' : '🔴 分散');
    console.log(`  ${labels[j].padEnd(10)}: ${mn} ~ ${mx} (avg ${avg.toFixed(1)}) ${st}`);
  }

  // Phase 搜尋統計
  console.log('\n  📊 搜尋統計:');
  console.log(`  Phase 1A (CCI):     ${gridCCI.length} 組合`);
  console.log(`  Phase 1B (ATR):     ${gridATR.length} 組合`);
  console.log(`  Phase 1C (EMA):     ${gridEMA.length} 組合`);
  console.log(`  Phase 2  (精搜):    ${gridFine.length} 組合`);
  console.log(`  總計:               ${gridCCI.length + gridATR.length + gridEMA.length + gridFine.length} 組合`);

  // 儲存
  const out = path.join(__dirname, 'optimize-results.json');
  fs.writeFileSync(out, JSON.stringify({
    phases: {
      '1A_CCI': { grid: gridCCI.length, top3: resCCI.slice(0,3).map(r=>({params:r.params,stats:r.stats,score:r.score})) },
      '1B_ATR': { grid: gridATR.length, top3: resATR.slice(0,3).map(r=>({params:r.params,stats:r.stats,score:r.score})) },
      '1C_EMA': { grid: gridEMA.length, top3: resEMA.slice(0,3).map(r=>({params:r.params,stats:r.stats,score:r.score})) },
      '2_Fine': { grid: gridFine.length, top10: resFine.slice(0,10).map(r=>({params:r.params,stats:r.stats,score:r.score})) },
    },
    bestParams: bp, bestStats: bs, bestScore: best.score,
    defaultParams: dp, defaultStats: ds, defaultScore: dsc,
  }, null, 2));
  console.log(`\n💾 結果已儲存: ${out}`);
}

main();
