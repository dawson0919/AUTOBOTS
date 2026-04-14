---
title: Pionex Bot
tags: [project, trading, crypto, live]
project: pionex-bot
status: live
port: 5000
---

# 💹 Pionex Bot

自動化加密貨幣 + 商品期貨網格交易系統。包含 9 支 MA-Cross 機器人 + 6 支 Q-SIGNALS 共識驅動機器人 + Web Dashboard。

## 🚀 啟動指令
```bash
cd f:/AUTOBOTS/pionex-bot
"C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe" dashboard.py
```

訪問：http://localhost:5000 （Token: `autobots-2026`）

## 🤖 機器人總覽

### MA-Cross Grid Bots（9 支，原系統）
從 [bots.toml](file:///f:/AUTOBOTS/pionex-bot/bots.toml) 讀取設定：
- BTC ($80) / ETH / DOGE ($35) / SOL / PAXG / XAUT / SLVX / QQQX / USOX

### Q-SIGNALS Virtual Bots（6 支，新增 LIVE）
從 [bots_qsignals.toml](file:///f:/AUTOBOTS/pionex-bot/bots_qsignals.toml) 讀取：
- qs_btc / qs_eth / qs_sol / qs_xaut / qs_paxg / qs_usox
- 每支 $50 × 5× 槓桿（總保證金 $300、名目 $1,500）

## 🧩 核心模組
- `dashboard.py` — Flask 前端 + API
- `signal_manager.py` — MA-Cross 訊號排程器
- `signal_manager_qsignals.py` — Q-SIGNALS 並行觀察器
- `qsignals_bot_manager.py` — Q-SIGNALS 實單執行器（LIVE）
- `qsignals_adapter.py` — Python ↔ Q-SIGNALS JS 橋接
- `strategies_qsignals/runner.js` — Node.js 策略執行器

## 🎯 主要 API
- `GET /api/bots` — bots.toml 設定
- `GET /api/qsignals-bots` — Q-SIGNALS 6 支實況
- `GET /api/pnl` — Pionex 帳戶 P&L
- `GET /api/qsignals` — 最新訊號比對

## 🔗 相關
- [[topics/trading-strategies]] — 10 個 Q-SIGNALS 策略詳解
- [[topics/risk-management]] — 倉位 + 槓桿
- [[topics/infrastructure]] — Server 啟動指令
