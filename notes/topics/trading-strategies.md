---
title: 交易策略總覽
tags: [topic, trading, strategy, qsignals]
---

# 🎯 Q-SIGNALS 10 策略總覽

從 [/f/Q-SIGNALS](file:///F:/Q-SIGNALS) 移植到 [[projects/pionex-bot]]，完整 JS 邏輯保真，不重寫。

## 策略清單

| Strategy ID | 中文名 | 類型 | 校準 Symbols |
|------------|-------|------|--------------|
| `dual_st_breakout` | 雙 SuperTrend 突破 | 趨勢 | BTC, ETH, SOL, XAU, CL |
| `donchian_trend` | 唐奇安通道趨勢 | 突破 | BTC, ETH, SOL |
| `dual_ema` | 雙 EMA 交叉 | 趨勢 | BTC, ETH |
| `granville_eth_4h` | Granville 法則 (ETH 4H) | 動能 | ETH |
| `ichimoku_cloud` | 一目均衡表 | 趨勢 | BTC, ETH |
| `ma60` | 60 日均線 | 趨勢 | BTC, ETH, SOL |
| `macd_ma` | MACD + MA | 動能 | BTC, ETH |
| `mean_reversion` | 均值回歸 (BB+RSI) | 反轉 | BTC, XAU, PAXG |
| `three_style` | 三刀流 | 複合 | BTC, ETH, CL |
| `turtle_breakout` | 海龜交易法 | 突破 | BTC, ETH, SOL |

## 整合架構

```
[Python] qsignals_adapter.py
   ↓ subprocess
[Node.js] strategies_qsignals/runner.js
   ↓ require
[Q-SIGNALS] qsignals_src/engine/strategies/*.js
   ↓ uses
[Q-SIGNALS] qsignals_src/engine/indicators.js (436 lines)
```

## 共識決策（qsignals_bot_manager.py）

每個 bot：
1. 抓 OHLCV (Pionex 60M klines, 300 根)
2. 跑該 symbol 所有校準過的策略
3. 多數決 → LONG / SHORT / FLAT
4. 與當前方向比對 → 翻轉則：取消舊 grid → 建新方向 grid
5. **2 小時冷卻期**避免震盪過度交易

## 串關策略（NBA Predictor）

| 類型 | Edge 門檻 | 勝率門檻 | 其他要求 |
|------|----------|---------|---------|
| 🛡️ 最穩 2 關 | ≥ 5 分 | ≥ 60% | 不同場次 |
| ⚖️ 平衡 3 關 | ≥ 4 分 | ≥ 58% | 至少 1 腿大小球 |
| 💥 爆冷 3 關 | ≥ 3 分 | — | 容許冷門腿 |

API：`GET /api/nba/parlays/suggest`

## 🔗 相關
- [[projects/pionex-bot]]
- [[projects/nba-predictor]]
- [[topics/risk-management]]
