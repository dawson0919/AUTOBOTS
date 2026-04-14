---
title: 基礎設施 / Server 啟動
tags: [topic, infrastructure, ops]
---

# 🛠️ Infrastructure & Server Cheatsheet

## 🟢 Live Servers

| Server | Port | URL | 啟動指令 |
|--------|------|-----|---------|
| **NBA Predictor** | 8000 | http://localhost:8000 | `cd nba-predictor && PORT=8000 python app.py` |
| **Pionex Dashboard** | 5000 | http://localhost:5000 | `cd pionex-bot && python dashboard.py` |
| **Hermes Gateway** | (none) | Telegram bot | `cd hermes-agent && hermes gateway run` |

> 註：Python 路徑用 `C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe`（Python 3.14 launcher 有問題）

## 🤖 Background Workers

| Worker | 頻率 | 命令 |
|--------|------|------|
| NBA agent | 每小時 | `python agent.py --loop` |
| Q-SIGNALS bot manager | 每 60 min | `python qsignals_bot_manager.py --loop --interval 60` |
| Q-SIGNALS observer | 每 60 min | `python signal_manager_qsignals.py --loop --interval 60` |

## 🔑 API Keys / Tokens

| 服務 | 位置 |
|------|------|
| Supabase | `nba-predictor/.env` → SUPABASE_URL / SUPABASE_KEY |
| Pionex | `~/.pionex/config.toml` → api_key / secret_key |
| Pionex Dashboard | env `DASHBOARD_TOKEN` (預設 `autobots-2026`) |
| Gemini (Nano Banana) | `~/.nano-banana/.env` → GEMINI_API_KEY |
| OpenRouter (Hermes) | `hermes-agent/.env` |

## 🗂️ Working Directories

```
f:\AUTOBOTS\
├── ai-trading-claude\         (Claude skills)
├── andrej-karpathy-skills\    (Claude skills)
├── hermes-agent\              (AI agent framework)
├── MLB\                       (棒球預測模組)
├── nano-banana-2-skill\       (圖像生成 CLI)
├── nba-predictor\             (NBA Flask app + Supabase)
│   ├── app.py
│   ├── agent.py
│   ├── api/, models/, scrapers/, templates/
├── pionex-bot\                (網格交易系統)
│   ├── bots.toml              (9 MA-Cross bots)
│   ├── bots_qsignals.toml     (6 Q-SIGNALS bots, LIVE)
│   ├── dashboard.py
│   ├── qsignals_bot_manager.py
│   └── strategies_qsignals/
├── notes\                     (← 你在這個 Vault)
└── .obsidian\                 (Obsidian config)
```

## ⚡ 常用快速指令

```bash
# 重啟所有 server
powershell -Command "Get-Process python | Stop-Process -Force"
# ...再個別啟動

# 殺所有 hermes gateway 進程
powershell -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*hermes*gateway*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
```
