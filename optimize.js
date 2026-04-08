/**
 * optimize.js — Triple CCI 參數最佳化（高速版）
 *
 * 核心優化：預計算所有指標組合，回測只做信號掃描和倉位模擬
 *
 * node optimize.js             # 完整搜尋
 * node optimize.js --fast      # 快速模式（粗網格）
 * node optimize.js --top 20    # 顯示 Top 20
 */

const fs = require('fs');
const path = require('path');
const { CCI, EMA, RSI, MACD } = require('./cryptobot/node_modules/technicalindicators');

const CSV_PATH = path.join(__dirname, 'COMEX_GC1!, 60.csv');
const INITIAL_BALANCE = 100000;
const RISK_PER_TRADE = 0.02;
const COMMISSION = 0.0002;
const SLIPPAGE = 0.5;

const TOP_N = parseInt(parseArg('--top')) || 15;
const FAST_MODE = process.argv.includes('--fast');

function parseArg(flag) {
  const idx = process.argv.indexOf(flag);
  return idx !== -1 ? process.argv[idx + 1] : null;
}

// ─────────────────────────────────────────────
//  載入 CSV
// ─────────────────────────────────────────────
function loadCSV(fp) {
  const lines = fs.readFileSync(fp, 'utf8').trim().split('\n').slice(1);
  return lines.map(l => {
    const c = l.split(',');
    return { time: +c[0], open: +c[1], high: +c[2], low: +c[3], close: +c[4] };
  });
}

// ─────────────────────────────────────────────
//  預計算 ATR（Wilder smoothing）
// ─────────────────────────────────────────────
function preCalcATR(candles, period = 14) {
  const atrArr = new Float64Array(candles.length);
  const trs = [];
  for (let i = 1; i < candles.length; i++) {
    const h = candles[i].high, l = candles[i].low, pc = candles[i - 1].close;
    trs.push(Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc)));
  }
  let atr = 0;
  for (let i = 0; i < period && i < trs.length; i++) atr += trs[i];
  atr /= period;
  atrArr[period] = atr;
  for (let i = period; i < trs.length; i++) {
    atr = (atr * (period - 1) + trs[i]) / period;
    atrArr[i + 1] = atr;
  }
  return atrArr;
}

// ─────────────────────────────────────────────
//  預計算 Pseudo MFI
// ─────────────────────────────────────────────
function preCalcPseudoMFI(candles, period) {
  const mfi = new Float64Array(candles.length);
  for (let i = period; i < candles.length; i++) {
    let posFlow = 0, negFlow = 0;
    for (let j = i - period + 1; j <= i; j++) {
      const range = candles[j].high - candles[j].low;
      if (range === 0) continue;
      const dir = (candles[j].close - candles[j].open) / range;
      if (dir > 0) posFlow += dir * range;
      else negFlow += Math.abs(dir) * range;
    }
    const total = posFlow + negFlow;
    mfi[i] = total > 0 ? (posFlow / total) * 100 : 50;
  }
  return mfi;
}

// ─────────────────────────────────────────────
//  預計算所有需要的指標
// ─────────────────────────────────────────────
function preComputeAll(candles) {
  const closes = candles.map(c => c.close);
  const highs = candles.map(c => c.high);
  const lows = candles.map(c => c.low);
  const n = candles.length;

  console.log('  📐 預計算指標...');

  // CCI 各週期
  const cciPeriods = [10, 12, 14, 16, 18, 20, 25, 30, 35, 40, 50, 60, 70];
  const cciCache = {};
  for (const p of cciPeriods) {
    const raw = CCI.calculate({ high: highs, low: lows, close: closes, period: p });
    // 對齊到原始陣列長度（前面補 NaN）
    const arr = new Float64Array(n).fill(NaN);
    const offset = n - raw.length;
    for (let i = 0; i < raw.length; i++) arr[offset + i] = raw[i];
    cciCache[p] = arr;
  }

  // EMA 各週期
  const emaPeriods = [10, 15, 20, 25, 30, 40, 50, 60, 70];
  const emaCache = {};
  for (const p of emaPeriods) {
    const raw = EMA.calculate({ period: p, values: closes });
    const arr = new Float64Array(n).fill(NaN);
    const offset = n - raw.length;
    for (let i = 0; i < raw.length; i++) arr[offset + i] = raw[i];
    emaCache[p] = arr;
  }

  // ATR
  const atr = preCalcATR(candles, 14);

  // MFI
  const mfi = preCalcPseudoMFI(candles, 14);

  console.log('  ✅ 指標預計算完成');
  return { cciCache, emaCache, atr, mfi, closes, highs, lows };
}

// ─────────────────────────────────────────────
//  快速回測（使用預計算指標）
// ─────────────────────────────────────────────
function fastBacktest(candles, indicators, params) {
  const { cciCache, emaCache, atr, mfi, closes, highs, lows } = indicators;
  const { cciFast, cciMid, cciSlow, slMult, tpMult, emaTrend, emaTrail } = params;

  const cciFArr = cciCache[cciFast];
  const cciMArr = cciCache[cciMid];
  const cciSArr = cciCache[cciSlow];
  const emaTArr = emaCache[emaTrend];
  const emaRArr = emaCache[emaTrail];

  if (!cciFArr || !cciMArr || !cciSArr || !emaTArr || !emaRArr) return null;

  let balance = INITIAL_BALANCE;
  let peakEquity = INITIAL_BALANCE;
  let maxDrawdownPct = 0;
  let position = null;
  const trades = [];

  const start = 70;  // 確保所有指標有效

  for (let i = start; i < candles.length; i++) {
    const price = closes[i];
    const hi = highs[i];
    const lo = lows[i];
    const ccf = cciFArr[i], ccfPrev = cciFArr[i - 1];
    const ccm = cciMArr[i];
    const ccs = cciSArr[i];
    const emaT = emaTArr[i];
    const emaR = emaRArr[i];
    const mfiVal = mfi[i];
    const atrVal = atr[i];

    if (isNaN(ccf) || isNaN(ccfPrev) || isNaN(ccm) || isNaN(ccs) || isNaN(emaT) || isNaN(emaR) || atrVal <= 0) continue;

    // ── 有持倉：追蹤 + SL 檢查 ──
    if (position) {
      const isLong = position.side === 1;

      // 更新追蹤
      if (position.trailing) {
        if (isLong && emaR > position.trailSL) position.trailSL = emaR;
        if (!isLong && emaR < position.trailSL) position.trailSL = emaR;
      } else {
        // 檢查是否啟動追蹤
        const fp = isLong ? price - position.entry : position.entry - price;
        if (fp >= atrVal * tpMult) {
          position.trailing = true;
          position.trailSL = emaR;
        }
      }

      let closed = false;

      // 追蹤止損
      if (position.trailing) {
        if ((isLong && lo <= position.trailSL) || (!isLong && hi >= position.trailSL)) {
          close(position.trailSL, 'TR');
          closed = true;
        }
      }

      // 固定止損
      if (!closed) {
        if ((isLong && lo <= position.sl) || (!isLong && hi >= position.sl)) {
          close(position.sl, 'SL');
          closed = true;
        }
      }

      // 信號平倉
      if (!closed && position) {
        const exitL = ccf < 0 && ccfPrev >= 0;
        const exitS = ccf > 0 && ccfPrev <= 0;
        if ((isLong && exitL) || (!isLong && exitS)) {
          close(price, 'EX');
          closed = true;
        }
      }
    }

    // ── 無持倉：進場 ──
    if (!position) {
      // 做多
      const longCCI1 = ccfPrev <= 0 && ccf > 0;
      const longCCI2 = ccm > 0;
      const longCCI3 = ccs > -100;
      const longTrend = price > emaT;
      const longMFI = mfiVal > 40;
      const longScore = (longCCI1 ? 1 : 0) + (longCCI2 ? 1 : 0) + (longCCI3 ? 1 : 0) + (longTrend ? 1 : 0) + (longMFI ? 1 : 0);

      // 做空
      const shortCCI1 = ccfPrev >= 0 && ccf < 0;
      const shortCCI2 = ccm < 0;
      const shortCCI3 = ccs < 100;
      const shortTrend = price < emaT;
      const shortMFI = mfiVal < 60;
      const shortScore = (shortCCI1 ? 1 : 0) + (shortCCI2 ? 1 : 0) + (shortCCI3 ? 1 : 0) + (shortTrend ? 1 : 0) + (shortMFI ? 1 : 0);

      let side = 0;
      if (longScore >= 4 && longCCI1) side = 1;
      else if (shortScore >= 4 && shortCCI1) side = -1;
      else if (longScore >= 3 && longCCI1) side = 1;
      else if (shortScore >= 3 && shortCCI1) side = -1;

      if (side !== 0 && atrVal > 0) {
        const isLong = side === 1;
        const entry = price + (isLong ? SLIPPAGE : -SLIPPAGE);
        const sl = isLong ? entry - atrVal * slMult : entry + atrVal * slMult;
        const slDist = Math.abs(entry - sl);
        if (slDist > 0) {
          const qty = (balance * RISK_PER_TRADE) / slDist;
          const posVal = qty * entry;
          if (posVal <= balance * 20) {
            balance -= posVal * COMMISSION;
            position = { side, entry, qty, sl, trailing: false, trailSL: 0 };
          }
        }
      }
    }

    // 權益追蹤
    const unrealized = position
      ? (position.side === 1 ? price - position.entry : position.entry - price) * position.qty
      : 0;
    const equity = balance + unrealized;
    if (equity > peakEquity) peakEquity = equity;
    const ddPct = (peakEquity - equity) / peakEquity;
    if (ddPct > maxDrawdownPct) maxDrawdownPct = ddPct;
  }

  // 強制平倉
  if (position) close(closes[candles.length - 1], 'END');

  function close(exitPrice, reason) {
    if (!position) return;
    const isLong = position.side === 1;
    const slip = isLong ? -SLIPPAGE : SLIPPAGE;
    const actual = exitPrice + slip;
    const pnl = isLong
      ? (actual - position.entry) * position.qty
      : (position.entry - actual) * position.qty;
    const comm = position.qty * actual * COMMISSION;
    const net = pnl - comm;
    balance += net;
    const ret = isLong
      ? (actual - position.entry) / position.entry * 100
      : (position.entry - actual) / position.entry * 100;
    trades.push({ pnl: net, ret, reason });
    position = null;
  }

  // 統計
  const total = trades.length;
  if (total < 5) return null;

  const wins = trades.filter(t => t.pnl > 0);
  const losses = trades.filter(t => t.pnl <= 0);
  const gp = wins.reduce((s, t) => s + t.pnl, 0);
  const gl = Math.abs(losses.reduce((s, t) => s + t.pnl, 0));
  const pf = gl > 0 ? gp / gl : 99;
  const wr = wins.length / total * 100;
  const totalRet = (balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100;
  const totalPnl = trades.reduce((s, t) => s + t.pnl, 0);
  const avgW = wins.length > 0 ? gp / wins.length : 0;
  const avgL = losses.length > 0 ? gl / losses.length : 0;

  const rets = trades.map(t => t.ret);
  const avgR = rets.reduce((s, r) => s + r, 0) / rets.length;
  const stdR = Math.sqrt(rets.reduce((s, r) => s + (r - avgR) ** 2, 0) / rets.length);
  const sharpe = stdR > 0 ? avgR / stdR : 0;

  const mdd = maxDrawdownPct * 100;
  const trailCount = trades.filter(t => t.reason === 'TR').length;

  return { total, wins: wins.length, losses: losses.length, wr, totalRet, totalPnl, pf, sharpe, mdd, avgW, avgL, balance, trailCount };
}

// ─────────────────────────────────────────────
//  評分
// ─────────────────────────────────────────────
function score(s) {
  if (!s) return -Infinity;
  const pf = Math.min(s.pf, 3) / 3;
  const ret = Math.min(Math.max(s.totalRet, -50), 100) / 100;
  const sh = Math.min(Math.max(s.sharpe, 0), 1);
  const mdd = Math.max(0, (50 - s.mdd)) / 50;
  const wr = s.wr / 100;
  return pf * 0.30 + ret * 0.25 + sh * 0.20 + mdd * 0.15 + wr * 0.10;
}

// ─────────────────────────────────────────────
//  MAIN
// ─────────────────────────────────────────────
function main() {
  console.log('📦 載入資料...');
  const candles = loadCSV(CSV_PATH);
  console.log(`✅ ${candles.length} 根 K 棒\n`);

  const indicators = preComputeAll(candles);

  // 參數空間
  const cciFastR   = FAST_MODE ? [10, 14, 18]        : [10, 12, 14, 16, 18, 20];
  const cciMidR    = FAST_MODE ? [20, 25, 30, 35]    : [20, 25, 30, 35];
  const cciSlowR   = FAST_MODE ? [40, 50, 60]        : [40, 50, 60, 70];
  const slMultR    = FAST_MODE ? [1.25, 1.5, 1.75, 2.0]  : [1.0, 1.25, 1.5, 1.75, 2.0, 2.25];
  const tpMultR    = FAST_MODE ? [1.75, 2.25, 3.0]   : [1.5, 1.75, 2.0, 2.25, 2.5, 3.0, 3.5];
  const emaTrendR  = FAST_MODE ? [30, 50, 70]        : [30, 40, 50, 60, 70];
  const emaTrailR  = FAST_MODE ? [10, 20, 30]        : [10, 15, 20, 25, 30];

  // 建立網格
  const grid = [];
  for (const cciFast of cciFastR)
    for (const cciMid of cciMidR) { if (cciMid <= cciFast) continue;
      for (const cciSlow of cciSlowR) { if (cciSlow <= cciMid) continue;
        for (const slMult of slMultR)
          for (const tpMult of tpMultR) { if (tpMult <= slMult) continue;
            for (const emaTrend of emaTrendR)
              for (const emaTrail of emaTrailR) { if (emaTrail >= emaTrend) continue;
                grid.push({ cciFast, cciMid, cciSlow, slMult, tpMult, emaTrend, emaTrail });
              }
          }
      }
    }

  console.log(`\n🔍 搜尋空間: ${grid.length} 組參數 (${FAST_MODE ? '快速' : '完整'})`);
  console.log('⏳ 開始最佳化...\n');

  const results = [];
  const t0 = Date.now();
  let lastPrint = 0;

  for (let i = 0; i < grid.length; i++) {
    const p = grid[i];
    const stats = fastBacktest(candles, indicators, p);
    if (stats) results.push({ params: p, stats, score: score(stats) });

    if (Date.now() - lastPrint > 3000 || i === grid.length - 1) {
      const pct = ((i + 1) / grid.length * 100).toFixed(1);
      const elapsed = ((Date.now() - t0) / 1000).toFixed(0);
      const speed = ((i + 1) / ((Date.now() - t0) / 1000)).toFixed(0);
      process.stdout.write(`\r  進度: ${pct}% (${i + 1}/${grid.length}) | 有效: ${results.length} | ${speed} 組/秒 | ${elapsed}s`);
      lastPrint = Date.now();
    }
  }

  const totalSec = ((Date.now() - t0) / 1000).toFixed(1);
  console.log(`\n\n✅ 完成！耗時 ${totalSec}s | 有效: ${results.length}/${grid.length}\n`);

  results.sort((a, b) => b.score - a.score);

  // ── Top N 表格 ──
  console.log('═'.repeat(135));
  console.log(`  🏆 Top ${TOP_N} 參數組合`);
  console.log('═'.repeat(135));
  console.log(
    `  ${'#'.padStart(3)} | ${'CCI'.padEnd(12)} | ${'SL'.padStart(5)} | ${'TP'.padStart(5)} | ` +
    `${'EMA'.padStart(4)} | ${'Tr'.padStart(3)} | ` +
    `${'報酬%'.padStart(8)} | ${'勝率%'.padStart(6)} | ${'PF'.padStart(5)} | ${'Sharpe'.padStart(7)} | ` +
    `${'MDD%'.padStart(6)} | ${'筆數'.padStart(4)} | ${'追蹤'.padStart(4)} | ${'均獲利'.padStart(8)} | ${'均虧損'.padStart(8)} | ${'評分'.padStart(7)}`
  );
  console.log('  ' + '-'.repeat(131));

  const top = results.slice(0, TOP_N);
  for (let i = 0; i < top.length; i++) {
    const { params: p, stats: s, score: sc } = top[i];
    console.log(
      `  ${String(i + 1).padStart(3)} | ${`${p.cciFast}/${p.cciMid}/${p.cciSlow}`.padEnd(12)} | ` +
      `${p.slMult.toFixed(2).padStart(5)} | ${p.tpMult.toFixed(2).padStart(5)} | ` +
      `${String(p.emaTrend).padStart(4)} | ${String(p.emaTrail).padStart(3)} | ` +
      `${(s.totalRet >= 0 ? '+' : '') + s.totalRet.toFixed(1)}%`.padStart(8) + ` | ${s.wr.toFixed(1).padStart(5)}% | ` +
      `${s.pf.toFixed(2).padStart(5)} | ${s.sharpe.toFixed(3).padStart(7)} | ` +
      `${s.mdd.toFixed(1).padStart(5)}% | ${String(s.total).padStart(4)} | ${String(s.trailCount).padStart(4)} | ` +
      `$${s.avgW.toFixed(0).padStart(6)} | $${s.avgL.toFixed(0).padStart(6)} | ${sc.toFixed(4).padStart(7)}`
    );
  }
  console.log('═'.repeat(135));

  // ── 最佳 vs 預設 ──
  const best = top[0];
  const dp = { cciFast: 14, cciMid: 25, cciSlow: 50, slMult: 1.75, tpMult: 2.25, emaTrend: 50, emaTrail: 20 };
  const ds = fastBacktest(candles, indicators, dp);
  const dsc = score(ds);

  const bp = best.params;
  const bs = best.stats;

  console.log('\n' + '═'.repeat(65));
  console.log('  📊 最佳參數 vs 預設參數');
  console.log('═'.repeat(65));
  console.log(`\n  預設: CCI(14/25/50) SL=1.75 TP=2.25 EMA=50 Trail=20`);
  console.log(`  最佳: CCI(${bp.cciFast}/${bp.cciMid}/${bp.cciSlow}) SL=${bp.slMult} TP=${bp.tpMult} EMA=${bp.emaTrend} Trail=${bp.emaTrail}\n`);

  const rows = [
    ['總報酬率',   `${ds.totalRet.toFixed(1)}%`,  `${bs.totalRet.toFixed(1)}%`,  `${(bs.totalRet - ds.totalRet) >= 0 ? '+' : ''}${(bs.totalRet - ds.totalRet).toFixed(1)}%`],
    ['總損益',     `$${ds.totalPnl.toFixed(0)}`,   `$${bs.totalPnl.toFixed(0)}`,  `$${(bs.totalPnl - ds.totalPnl) >= 0 ? '+' : ''}${(bs.totalPnl - ds.totalPnl).toFixed(0)}`],
    ['勝率',       `${ds.wr.toFixed(1)}%`,         `${bs.wr.toFixed(1)}%`,        `${(bs.wr - ds.wr) >= 0 ? '+' : ''}${(bs.wr - ds.wr).toFixed(1)}%`],
    ['盈虧比(PF)', `${ds.pf.toFixed(2)}`,          `${bs.pf.toFixed(2)}`,         `${(bs.pf - ds.pf) >= 0 ? '+' : ''}${(bs.pf - ds.pf).toFixed(2)}`],
    ['Sharpe',     `${ds.sharpe.toFixed(3)}`,       `${bs.sharpe.toFixed(3)}`,     `${(bs.sharpe - ds.sharpe) >= 0 ? '+' : ''}${(bs.sharpe - ds.sharpe).toFixed(3)}`],
    ['最大回撤',   `${ds.mdd.toFixed(1)}%`,        `${bs.mdd.toFixed(1)}%`,       `${(ds.mdd - bs.mdd) >= 0 ? '' : '-'}${Math.abs(ds.mdd - bs.mdd).toFixed(1)}%`],
    ['交易次數',   `${ds.total}`,                   `${bs.total}`,                 ``],
    ['追蹤止盈',   `${ds.trailCount}`,              `${bs.trailCount}`,            ``],
    ['綜合評分',   `${dsc.toFixed(4)}`,             `${best.score.toFixed(4)}`,    `+${(best.score - dsc).toFixed(4)}`],
  ];

  console.log(`  ${'指標'.padEnd(12)} | ${'預設'.padStart(14)} | ${'最佳'.padStart(14)} | ${'改善'.padStart(12)}`);
  console.log('  ' + '-'.repeat(58));
  for (const [label, d, b, diff] of rows) {
    console.log(`  ${label.padEnd(12)} | ${d.padStart(14)} | ${b.padStart(14)} | ${diff.padStart(12)}`);
  }
  console.log('═'.repeat(65));

  // ── 穩健性分析 ──
  const top5 = top.slice(0, 5);
  console.log('\n  📐 穩健性分析（Top 5 參數收斂區間）:');
  const pnames = ['cciFast', 'cciMid', 'cciSlow', 'slMult', 'tpMult', 'emaTrend', 'emaTrail'];
  const labels = ['CCI Fast', 'CCI Mid', 'CCI Slow', 'ATR SL', 'ATR TP', 'EMA 趨勢', 'Trail EMA'];
  for (let j = 0; j < pnames.length; j++) {
    const vals = top5.map(r => r.params[pnames[j]]);
    const mn = Math.min(...vals), mx = Math.max(...vals);
    const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
    const st = mn === mx ? '✅ 收斂' : (mx - mn <= avg * 0.3 ? '🟡 窄幅' : '🔴 分散');
    console.log(`  ${labels[j].padEnd(10)}: ${mn} ~ ${mx} (avg ${avg.toFixed(1)}) ${st}`);
  }

  // 儲存
  const out = path.join(__dirname, 'optimize-results.json');
  fs.writeFileSync(out, JSON.stringify({
    totalCombinations: grid.length, validResults: results.length, timeSeconds: totalSec,
    bestParams: bp, bestStats: bs, bestScore: best.score,
    defaultParams: dp, defaultStats: ds, defaultScore: dsc,
    top15: top.map(r => ({ params: r.params, stats: r.stats, score: r.score })),
  }, null, 2));
  console.log(`\n💾 結果已儲存: ${out}`);
}

main();
