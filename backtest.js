/**
 * backtest.js
 * 回測引擎 — 讀取 CSV K 線資料，執行多策略回測
 *
 * 使用方式：
 *   node backtest.js                         # 全部策略
 *   node backtest.js --strategy threeBlade   # 只跑 ThreeBlade
 *   node backtest.js --strategy tripleCCI    # 只跑 Triple CCI
 *   node backtest.js --strategy all          # 全部策略
 */

const fs = require('fs');
const path = require('path');
const ThreeBladeStrategy = require('./cryptobot/strategy/threeBlade');
const TrinityStrategy = require('./cryptobot/strategy/trinity');
const TripleCCIStrategy = require('./cryptobot/strategy/tripleCCI');
const { calcATR, calcPnl, calcReturnPct } = require('./cryptobot/utils/indicators');
const { EMA } = require('./cryptobot/node_modules/technicalindicators');

// ─────────────────────────────────────────────
//  CONFIG
// ─────────────────────────────────────────────
const CONFIG = {
  csvPath: path.join(__dirname, 'COMEX_GC1!, 60.csv'),
  initialBalance: 100000,
  riskPerTrade: 0.02,
  atrPeriod: 14,
  commission: 0.0002,
  slippage: 0.5,
  strategy: parseArg('--strategy') || 'all',
};

// 每策略可有不同 SL/TP 設定
const STRATEGY_PARAMS = {
  threeBlade: { slMult: 1.5,  tpMult: 2.5,  trailing: false },
  trinity:    { slMult: 1.5,  tpMult: 2.5,  trailing: false },
  tripleCCI:  { slMult: 1.75, tpMult: 2.25, trailing: true, trailingEMA: 20 },
};

function parseArg(flag) {
  const idx = process.argv.indexOf(flag);
  return idx !== -1 ? process.argv[idx + 1] : null;
}

// ─────────────────────────────────────────────
//  CSV 讀取
// ─────────────────────────────────────────────
function loadCSV(filePath) {
  const raw = fs.readFileSync(filePath, 'utf8').trim();
  const lines = raw.split('\n');
  return lines.slice(1).map(line => {
    const cols = line.split(',');
    return {
      time: parseInt(cols[0]),
      open: parseFloat(cols[1]),
      high: parseFloat(cols[2]),
      low: parseFloat(cols[3]),
      close: parseFloat(cols[4]),
    };
  });
}

// ─────────────────────────────────────────────
//  回測引擎（支援追蹤止盈）
// ─────────────────────────────────────────────
class Backtester {
  constructor(strategy, candles, config, stratParams) {
    this.strategy = strategy;
    this.candles = candles;
    this.config = config;
    this.stratParams = stratParams;

    this.balance = config.initialBalance;
    this.equity = config.initialBalance;
    this.peakEquity = config.initialBalance;

    // position: { side, entryPrice, qty, sl, tp, entryTime, entryBar, atr, trailingActive, trailingSL }
    this.position = null;
    this.trades = [];
    this.equityCurve = [];
    this.maxDrawdown = 0;
    this.maxDrawdownPct = 0;
  }

  run() {
    const lookback = 60;

    for (let i = lookback; i < this.candles.length; i++) {
      const slice = this.candles.slice(0, i + 1);
      const currentCandle = this.candles[i];

      // 1. 有持倉 → 更新追蹤止盈 → 檢查止損/止盈/追蹤
      if (this.position) {
        if (this.stratParams.trailing) {
          this._updateTrailingStop(slice, currentCandle);
        }
        this._checkSLTP(currentCandle, i);
      }

      // 2. 無持倉 → 檢查進場信號
      if (!this.position) {
        const result = this.strategy.analyze(slice);
        this._processSignal(result, currentCandle, slice, i);
      } else {
        // 3. 有持倉 → 檢查策略平倉信號
        const result = this.strategy.analyze(slice);
        this._processExitSignal(result, currentCandle, i);
      }

      // 4. 更新權益曲線
      const unrealizedPnl = this.position
        ? this._calcUnrealizedPnl(currentCandle.close)
        : 0;
      this.equity = this.balance + unrealizedPnl;

      if (this.equity > this.peakEquity) this.peakEquity = this.equity;
      const dd = this.peakEquity - this.equity;
      const ddPct = dd / this.peakEquity;
      if (dd > this.maxDrawdown) this.maxDrawdown = dd;
      if (ddPct > this.maxDrawdownPct) this.maxDrawdownPct = ddPct;

      this.equityCurve.push({ time: currentCandle.time, equity: this.equity, balance: this.balance });
    }

    // 回測結束強制平倉
    if (this.position) {
      const lastCandle = this.candles[this.candles.length - 1];
      this._closeTrade(lastCandle.close, lastCandle.time, this.candles.length - 1, 'END_OF_DATA');
    }

    return this._generateReport();
  }

  _processSignal(result, candle, slice, barIndex) {
    // 接受 STRONG 和 WEAK（score >= 3）信號
    const isStrong = result.signal.includes('STRONG');
    const isWeak = result.signal.includes('WEAK') && result.strength >= 3;
    if (!isStrong && !isWeak) return;

    const atr = calcATR(slice, this.config.atrPeriod);
    if (!atr || atr <= 0) return;

    const isLong = result.signal.includes('LONG');
    const entryPrice = candle.close + (isLong ? this.config.slippage : -this.config.slippage);

    const slMult = this.stratParams.slMult;
    const tpMult = this.stratParams.tpMult;

    const sl = isLong
      ? entryPrice - atr * slMult
      : entryPrice + atr * slMult;
    const tp = isLong
      ? entryPrice + atr * tpMult
      : entryPrice - atr * tpMult;

    // Fixed fraction position sizing
    const riskAmount = this.balance * this.config.riskPerTrade;
    const slDistance = Math.abs(entryPrice - sl);
    if (slDistance === 0) return;

    const qty = riskAmount / slDistance;
    const positionValue = qty * entryPrice;
    if (positionValue > this.balance * 20) return;

    const commission = positionValue * this.config.commission;
    this.balance -= commission;

    this.position = {
      side: isLong ? 'LONG' : 'SHORT',
      entryPrice,
      qty,
      sl,
      tp,                          // 追蹤模式下 tp = 啟動追蹤的門檻價
      entryTime: candle.time,
      entryBar: barIndex,
      atr,
      commission,
      trailingActive: false,       // 追蹤止盈是否已啟動
      trailingSL: null,            // 追蹤止損價位
      signalStrength: result.strength,
    };
  }

  /**
   * 追蹤止盈邏輯：
   * 1. 浮盈未達 ATR × tpMult → 正常 SL/TP
   * 2. 浮盈達到 ATR × tpMult → 啟動追蹤，用 EMA(20) 作為追蹤止損
   * 3. 追蹤啟動後：多單 → close < EMA(20) 平倉；空單 → close > EMA(20)
   */
  _updateTrailingStop(slice, candle) {
    if (!this.position) return;

    const pos = this.position;
    const isLong = pos.side === 'LONG';
    const price = candle.close;

    // 計算浮盈
    const floatingPnl = isLong
      ? price - pos.entryPrice
      : pos.entryPrice - price;

    const tpDistance = pos.atr * this.stratParams.tpMult;

    // 啟動追蹤
    if (!pos.trailingActive && floatingPnl >= tpDistance) {
      pos.trailingActive = true;

      // 用 EMA trailing 計算追蹤止損
      const closes = slice.map(c => parseFloat(c.close));
      const emaArr = EMA.calculate({
        period: this.stratParams.trailingEMA,
        values: closes,
      });
      if (emaArr.length > 0) {
        pos.trailingSL = emaArr[emaArr.length - 1];
      }
    }

    // 已啟動 → 每根 K 棒更新追蹤止損（只往有利方向移動）
    if (pos.trailingActive) {
      const closes = slice.map(c => parseFloat(c.close));
      const emaArr = EMA.calculate({
        period: this.stratParams.trailingEMA,
        values: closes,
      });
      if (emaArr.length > 0) {
        const newTrail = emaArr[emaArr.length - 1];
        if (isLong) {
          // 多單：追蹤止損只能往上
          if (pos.trailingSL === null || newTrail > pos.trailingSL) {
            pos.trailingSL = newTrail;
          }
        } else {
          // 空單：追蹤止損只能往下
          if (pos.trailingSL === null || newTrail < pos.trailingSL) {
            pos.trailingSL = newTrail;
          }
        }
      }
    }
  }

  _processExitSignal(result, candle, barIndex) {
    if (!this.position) return;
    const isLong = this.position.side === 'LONG';

    if (isLong && result.signal === 'EXIT_LONG') {
      this._closeTrade(candle.close, candle.time, barIndex, 'EXIT_SIGNAL');
    } else if (!isLong && result.signal === 'EXIT_SHORT') {
      this._closeTrade(candle.close, candle.time, barIndex, 'EXIT_SIGNAL');
    }
  }

  _checkSLTP(candle, barIndex) {
    if (!this.position) return;

    const { side, sl, tp, trailingActive, trailingSL } = this.position;
    const isLong = side === 'LONG';

    // 1. 追蹤止損（優先檢查）
    if (trailingActive && trailingSL !== null) {
      if (isLong && candle.low <= trailingSL) {
        this._closeTrade(trailingSL, candle.time, barIndex, 'TRAILING_STOP');
        return;
      } else if (!isLong && candle.high >= trailingSL) {
        this._closeTrade(trailingSL, candle.time, barIndex, 'TRAILING_STOP');
        return;
      }
    }

    // 2. 固定止損
    if (isLong && candle.low <= sl) {
      this._closeTrade(sl, candle.time, barIndex, 'STOP_LOSS');
      return;
    } else if (!isLong && candle.high >= sl) {
      this._closeTrade(sl, candle.time, barIndex, 'STOP_LOSS');
      return;
    }

    // 3. 非追蹤模式的固定止盈
    if (!this.stratParams.trailing) {
      if (isLong && candle.high >= tp) {
        this._closeTrade(tp, candle.time, barIndex, 'TAKE_PROFIT');
      } else if (!isLong && candle.low <= tp) {
        this._closeTrade(tp, candle.time, barIndex, 'TAKE_PROFIT');
      }
    }
    // 追蹤模式下不用固定止盈，由 _updateTrailingStop 管理
  }

  _closeTrade(exitPrice, exitTime, barIndex, reason) {
    const pos = this.position;
    if (!pos) return;

    const slippage = pos.side === 'LONG' ? -this.config.slippage : this.config.slippage;
    const actualExit = exitPrice + slippage;

    const pnl = pos.side === 'LONG'
      ? (actualExit - pos.entryPrice) * pos.qty
      : (pos.entryPrice - actualExit) * pos.qty;

    const commission = pos.qty * actualExit * this.config.commission;
    const netPnl = pnl - commission;
    this.balance += netPnl;

    const returnPct = pos.side === 'LONG'
      ? (actualExit - pos.entryPrice) / pos.entryPrice * 100
      : (pos.entryPrice - actualExit) / pos.entryPrice * 100;

    this.trades.push({
      side: pos.side,
      entryPrice: pos.entryPrice,
      exitPrice: actualExit,
      qty: pos.qty,
      pnl: netPnl,
      returnPct,
      reason,
      entryTime: new Date(pos.entryTime * 1000).toISOString(),
      exitTime: new Date(exitTime * 1000).toISOString(),
      holdingBars: barIndex - pos.entryBar,
      atr: pos.atr,
      trailingUsed: pos.trailingActive,
    });

    this.position = null;
  }

  _calcUnrealizedPnl(currentPrice) {
    if (!this.position) return 0;
    const { side, entryPrice, qty } = this.position;
    return side === 'LONG'
      ? (currentPrice - entryPrice) * qty
      : (entryPrice - currentPrice) * qty;
  }

  _generateReport() {
    const totalTrades = this.trades.length;
    if (totalTrades === 0) {
      return { strategyName: this.strategy.name, totalTrades: 0, message: '無交易' };
    }

    const wins = this.trades.filter(t => t.pnl > 0);
    const losses = this.trades.filter(t => t.pnl <= 0);
    const winRate = wins.length / totalTrades * 100;

    const totalPnl = this.trades.reduce((sum, t) => sum + t.pnl, 0);
    const avgPnl = totalPnl / totalTrades;
    const avgWin = wins.length > 0 ? wins.reduce((s, t) => s + t.pnl, 0) / wins.length : 0;
    const avgLoss = losses.length > 0 ? Math.abs(losses.reduce((s, t) => s + t.pnl, 0) / losses.length) : 0;

    const grossProfit = wins.reduce((s, t) => s + t.pnl, 0);
    const grossLoss = Math.abs(losses.reduce((s, t) => s + t.pnl, 0));
    const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : Infinity;

    const avgHoldingBars = this.trades.reduce((s, t) => s + t.holdingBars, 0) / totalTrades;

    const returns = this.trades.map(t => t.returnPct);
    const avgReturn = returns.reduce((s, r) => s + r, 0) / returns.length;
    const stdReturn = Math.sqrt(returns.reduce((s, r) => s + (r - avgReturn) ** 2, 0) / returns.length);
    const sharpe = stdReturn > 0 ? avgReturn / stdReturn : 0;

    const byReason = {};
    for (const t of this.trades) {
      if (!byReason[t.reason]) byReason[t.reason] = { count: 0, pnl: 0 };
      byReason[t.reason].count++;
      byReason[t.reason].pnl += t.pnl;
    }

    const longs = this.trades.filter(t => t.side === 'LONG');
    const shorts = this.trades.filter(t => t.side === 'SHORT');
    const longWins = longs.filter(t => t.pnl > 0).length;
    const shortWins = shorts.filter(t => t.pnl > 0).length;

    let maxConsecWins = 0, maxConsecLosses = 0, consecWins = 0, consecLosses = 0;
    for (const t of this.trades) {
      if (t.pnl > 0) { consecWins++; consecLosses = 0; maxConsecWins = Math.max(maxConsecWins, consecWins); }
      else { consecLosses++; consecWins = 0; maxConsecLosses = Math.max(maxConsecLosses, consecLosses); }
    }

    const maxWin = Math.max(...this.trades.map(t => t.pnl));
    const maxLoss = Math.min(...this.trades.map(t => t.pnl));
    const finalBalance = this.balance;
    const totalReturn = (finalBalance - this.config.initialBalance) / this.config.initialBalance * 100;

    const trailingCount = this.trades.filter(t => t.trailingUsed).length;

    return {
      strategyName: this.strategy.name,
      stratParams: this.stratParams,
      period: { from: this.trades[0].entryTime, to: this.trades[this.trades.length - 1].exitTime },
      initialBalance: this.config.initialBalance,
      finalBalance: +finalBalance.toFixed(2),
      totalReturn: +totalReturn.toFixed(2),
      totalPnl: +totalPnl.toFixed(2),
      totalTrades,
      wins: wins.length, losses: losses.length, winRate: +winRate.toFixed(1),
      avgPnl: +avgPnl.toFixed(2), avgWin: +avgWin.toFixed(2), avgLoss: +avgLoss.toFixed(2),
      profitFactor: profitFactor === Infinity ? '∞' : +profitFactor.toFixed(2),
      maxDrawdown: +this.maxDrawdown.toFixed(2), maxDrawdownPct: +(this.maxDrawdownPct * 100).toFixed(2),
      sharpeRatio: +sharpe.toFixed(3), avgHoldingBars: +avgHoldingBars.toFixed(1),
      maxWin: +maxWin.toFixed(2), maxLoss: +maxLoss.toFixed(2),
      maxConsecWins, maxConsecLosses,
      trailingCount,
      longTrades: { total: longs.length, wins: longWins, winRate: longs.length > 0 ? +(longWins / longs.length * 100).toFixed(1) : 0 },
      shortTrades: { total: shorts.length, wins: shortWins, winRate: shorts.length > 0 ? +(shortWins / shorts.length * 100).toFixed(1) : 0 },
      byReason: Object.fromEntries(Object.entries(byReason).map(([k, v]) => [k, { count: v.count, pnl: +v.pnl.toFixed(2) }])),
      trades: this.trades.map(t => ({
        ...t,
        entryPrice: +t.entryPrice.toFixed(2), exitPrice: +t.exitPrice.toFixed(2),
        qty: +t.qty.toFixed(4), pnl: +t.pnl.toFixed(2), returnPct: +t.returnPct.toFixed(2), atr: +t.atr.toFixed(2),
      })),
    };
  }
}

// ─────────────────────────────────────────────
//  輸出格式化
// ─────────────────────────────────────────────
function printReport(report) {
  if (report.totalTrades === 0) {
    console.log(`\n📊 ${report.strategyName}: 無交易信號產生\n`);
    return;
  }

  const sp = report.stratParams;
  console.log('\n' + '═'.repeat(65));
  console.log(`  📊 回測報告 — ${report.strategyName}`);
  console.log(`  ⚙️  SL=${sp.slMult}x ATR | TP=${sp.tpMult}x ATR | 追蹤=${sp.trailing ? 'EMA(' + sp.trailingEMA + ')' : '無'}`);
  console.log('═'.repeat(65));

  console.log(`\n  📅 回測期間: ${report.period.from.slice(0, 10)} ~ ${report.period.to.slice(0, 10)}`);
  console.log(`  💰 初始資金: $${report.initialBalance.toLocaleString()}`);
  console.log(`  💵 最終資金: $${report.finalBalance.toLocaleString()}`);
  console.log(`  📈 總報酬率: ${report.totalReturn >= 0 ? '+' : ''}${report.totalReturn}%`);
  console.log(`  💲 總損益:   $${report.totalPnl >= 0 ? '+' : ''}${report.totalPnl.toLocaleString()}`);

  console.log('\n  ─── 交易統計 ───');
  console.log(`  總交易次數:   ${report.totalTrades}`);
  console.log(`  勝率:         ${report.winRate}% (${report.wins}W / ${report.losses}L)`);
  console.log(`  平均損益:     $${report.avgPnl >= 0 ? '+' : ''}${report.avgPnl}`);
  console.log(`  平均獲利:     $+${report.avgWin}`);
  console.log(`  平均虧損:     $-${report.avgLoss}`);
  console.log(`  盈虧比 (PF):  ${report.profitFactor}`);
  console.log(`  Sharpe Ratio: ${report.sharpeRatio}`);

  console.log('\n  ─── 風險指標 ───');
  console.log(`  最大回撤:     $${report.maxDrawdown.toLocaleString()} (${report.maxDrawdownPct}%)`);
  console.log(`  最大單筆獲利: $+${report.maxWin.toLocaleString()}`);
  console.log(`  最大單筆虧損: $${report.maxLoss.toLocaleString()}`);
  console.log(`  最長連勝:     ${report.maxConsecWins} 次`);
  console.log(`  最長連敗:     ${report.maxConsecLosses} 次`);
  console.log(`  平均持倉:     ${report.avgHoldingBars} 根K棒 (${report.avgHoldingBars.toFixed(1)}h)`);
  if (report.trailingCount > 0) {
    console.log(`  追蹤止盈觸發: ${report.trailingCount} 次`);
  }

  console.log('\n  ─── 多空分析 ───');
  console.log(`  做多: ${report.longTrades.total} 筆, 勝率 ${report.longTrades.winRate}% (${report.longTrades.wins}W)`);
  console.log(`  做空: ${report.shortTrades.total} 筆, 勝率 ${report.shortTrades.winRate}% (${report.shortTrades.wins}W)`);

  console.log('\n  ─── 出場原因 ───');
  const reasonLabels = {
    'STOP_LOSS': '止損', 'TAKE_PROFIT': '止盈', 'EXIT_SIGNAL': '信號平倉',
    'TRAILING_STOP': '追蹤止盈', 'END_OF_DATA': '結束平倉',
  };
  for (const [reason, data] of Object.entries(report.byReason)) {
    const label = reasonLabels[reason] || reason;
    console.log(`  ${label}: ${data.count} 筆, PnL $${data.pnl >= 0 ? '+' : ''}${data.pnl.toLocaleString()}`);
  }

  // 最近 10 筆交易
  console.log('\n  ─── 最近 10 筆交易 ───');
  const recent = report.trades.slice(-10);
  console.log('  ' + '-'.repeat(100));
  console.log(`  ${'方向'.padEnd(4)} | ${'進場價'.padStart(9)} | ${'出場價'.padStart(9)} | ${'數量'.padStart(8)} | ${'損益'.padStart(10)} | ${'報酬%'.padStart(7)} | ${'原因'.padEnd(10)} | 持倉`);
  console.log('  ' + '-'.repeat(100));
  for (const t of recent) {
    const side = t.side === 'LONG' ? '多' : '空';
    const pnlStr = t.pnl >= 0 ? `+${t.pnl}` : `${t.pnl}`;
    const retStr = t.returnPct >= 0 ? `+${t.returnPct}%` : `${t.returnPct}%`;
    const reason = reasonLabels[t.reason] || t.reason;
    console.log(`  ${side.padEnd(4)} | ${String(t.entryPrice).padStart(9)} | ${String(t.exitPrice).padStart(9)} | ${String(t.qty).padStart(8)} | ${pnlStr.padStart(10)} | ${retStr.padStart(7)} | ${reason.padEnd(10)} | ${t.holdingBars}h`);
  }
  console.log('  ' + '-'.repeat(100));
  console.log('═'.repeat(65));
}

function printComparison(results) {
  const valid = results.filter(r => r.totalTrades > 0);
  if (valid.length < 2) return;

  console.log('\n' + '═'.repeat(70));
  console.log('  📊 策略比較表');
  console.log('═'.repeat(70));
  console.log(`  ${'指標'.padEnd(16)} | ${valid.map(r => r.strategyName.padStart(14)).join(' | ')}`);
  console.log('  ' + '-'.repeat(16 + valid.length * 17));

  const rows = [
    ['總報酬率',      r => `${r.totalReturn >= 0 ? '+' : ''}${r.totalReturn}%`],
    ['總損益',        r => `$${r.totalPnl >= 0 ? '+' : ''}${r.totalPnl}`],
    ['交易次數',      r => `${r.totalTrades}`],
    ['勝率',         r => `${r.winRate}%`],
    ['盈虧比(PF)',    r => `${r.profitFactor}`],
    ['Sharpe',       r => `${r.sharpeRatio}`],
    ['最大回撤',      r => `${r.maxDrawdownPct}%`],
    ['平均獲利',      r => `$${r.avgWin}`],
    ['平均虧損',      r => `$${r.avgLoss}`],
    ['做多勝率',      r => `${r.longTrades.winRate}%`],
    ['做空勝率',      r => `${r.shortTrades.winRate}%`],
    ['平均持倉',      r => `${r.avgHoldingBars}h`],
  ];

  for (const [label, fn] of rows) {
    console.log(`  ${label.padEnd(16)} | ${valid.map(r => fn(r).padStart(14)).join(' | ')}`);
  }
  console.log('═'.repeat(70));
}

// ─────────────────────────────────────────────
//  MAIN
// ─────────────────────────────────────────────
function main() {
  console.log('📦 載入 CSV 資料...');
  const candles = loadCSV(CONFIG.csvPath);
  console.log(`✅ 載入 ${candles.length} 根 60 分鐘 K 棒`);

  const startDate = new Date(candles[0].time * 1000).toISOString().slice(0, 10);
  const endDate = new Date(candles[candles.length - 1].time * 1000).toISOString().slice(0, 10);
  console.log(`📅 資料範圍: ${startDate} ~ ${endDate}`);
  console.log(`💲 價格範圍: $${Math.min(...candles.map(c => c.low)).toFixed(2)} ~ $${Math.max(...candles.map(c => c.high)).toFixed(2)}`);

  const strategyMap = {
    threeBlade: () => new ThreeBladeStrategy({ emaFast: 8, emaMid: 15, emaSlow: 30 }),
    trinity:    () => new TrinityStrategy({ rsiOversold: 35, rsiOverbought: 65, emaFast: 9, emaSlow: 21 }),
    tripleCCI:  () => new TripleCCIStrategy({
      cciFast: 14, cciMid: 25, cciSlow: 50,
      mfiLength: 14, emaTrend: 50, emaTrailing: 20,
      atrSL: 1.75, atrTPActivate: 2.25,
    }),
  };

  let keys;
  if (CONFIG.strategy === 'all') {
    keys = Object.keys(strategyMap);
  } else {
    keys = [CONFIG.strategy];
  }

  const results = [];

  for (const key of keys) {
    if (!strategyMap[key]) {
      console.log(`❌ 未知策略: ${key}`);
      continue;
    }
    const strat = strategyMap[key]();
    const params = STRATEGY_PARAMS[key];
    console.log(`\n🔄 回測 ${strat.name} | SL=${params.slMult}x TP=${params.tpMult}x ${params.trailing ? '+ 追蹤EMA(' + params.trailingEMA + ')' : ''}`);
    const bt = new Backtester(strat, candles, CONFIG, params);
    const report = bt.run();
    results.push(report);
    printReport(report);
  }

  // 策略比較表
  printComparison(results);

  // 儲存結果
  const outputPath = path.join(__dirname, 'backtest-results.json');
  const outputData = results.map(r => ({ ...r, trades: r.trades }));
  fs.writeFileSync(outputPath, JSON.stringify(outputData, null, 2), 'utf8');
  console.log(`\n💾 詳細結果已儲存: ${outputPath}`);
}

main();
