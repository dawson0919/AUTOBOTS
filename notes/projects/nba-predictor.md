---
title: NBA Predictor
tags: [project, sports, prediction, live]
project: nba-predictor
status: live
port: 8000
---

# 🏀 NBA Predictor

Flask + Supabase 會員制 NBA 比賽預測平台，含模型 vs 運彩 Edge 分析、自動串關建議、會員審核管理。

## 🚀 啟動指令
```bash
cd f:/AUTOBOTS/nba-predictor
PORT=8000 "C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe" app.py
```

訪問：
- 會員儀表板：http://localhost:8000/
- 管理後台：http://localhost:8000/admin
- 管理員：nbamoment@gmail.com

## 🧩 核心模組
- `app.py` — Flask app entry
- `agent.py` — 排程任務（每小時更新賽程 + 抓盤口 + 回測 + 推薦）
- `predictor.py` — Elo 模型 + ESPN 數據抓取
- `models/parlay.py` — 串關建議器（2/3 關 + EV 計算）
- `scrapers/playsport.py` — playsport.cc 盤口自動抓取
- `api/auth.py` — JWT 登入 / 註冊 / 重新申請
- `api/nba.py` — 預測 / 賠率 / picks 歷史 / 串關 API

## 🎯 主要 API
- `GET /api/nba/predictions` — 賽程 + 模型機率
- `GET /api/nba/odds` — 運彩盤口
- `POST /api/nba/picks/save` — 自動儲存推薦
- `POST /api/nba/picks/verify` — 用 ESPN 比分驗證
- `GET /api/nba/picks/stats` — 推薦勝率統計
- `GET /api/nba/parlays/suggest` — 串關建議

## 📊 當前數據
- 會員：115 人（57 approved / 47 rejected / 11 pending）
- Backtest：277 場、勝率 80.9%
- 推薦歷史：18/39 = 46.2%（讓分 37%、大小球 67%）

## 🔗 相關
- [[topics/member-management]] — 會員審核 + 退件流程
- [[topics/trading-strategies]] — 串關策略
