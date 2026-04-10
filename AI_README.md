# AUTOBOTS — AI 自動交易 + 運動預測系統

> 給 AI 助手的安裝指南：學員解壓縮後，請 AI 閱讀此文件並按步驟安裝。

## 快速安裝（AI 請執行以下步驟）

### Step 1: 環境需求確認
```bash
# 確認 Python 3.12+ 已安裝
python --version  # 需要 3.12 以上

# 確認 Node.js 已安裝（回測腳本需要）
node --version  # 需要 18+
```

### Step 2: 安裝 Python 依賴
```bash
cd AUTOBOTS
pip install flask httpx python-dotenv toml numpy xgboost
```

### Step 3: 設定 API 金鑰（選填）
```bash
# 複製範本
cp .env.example .env

# 編輯 .env 填入你的金鑰：
# PIONEX_API_KEY=你的Pionex API Key（交易功能需要）
# PIONEX_SECRET_KEY=你的Pionex Secret Key
# DASHBOARD_TOKEN=自訂密碼（預設 autobots-2026）
```

> **不填 API Key 也能使用**：Dashboard、NBA 預測、MLB 預測、BTC/ETH/WTI 信號都不需要 Pionex API Key。只有實際交易功能需要。

### Step 4: 啟動 Dashboard
```bash
cd pionex-bot
python dashboard.py --public
```

打開瀏覽器訪問 `http://localhost:5000`

### Step 5: 驗證所有頁面
| 頁面 | URL | 功能 |
|------|-----|------|
| Trading | http://localhost:5000/ | 交易主頁 — Pionex 網格機器人 P&L |
| Polymarket | http://localhost:5000/polymarket | Polymarket 預測市場瀏覽器 |
| NBA | http://localhost:5000/nba | NBA 預測 — Elo + XGBoost 模型 |
| BTC | http://localhost:5000/btc | BTC 價格信號 — Polymarket 數據 |
| ETH | http://localhost:5000/eth | ETH 價格信號 |
| WTI | http://localhost:5000/wti | 原油價格信號 |
| MLB | http://localhost:5000/mlb | MLB 預測 — Elo + 先發投手 ERA |

---

## 系統架構

```
AUTOBOTS/
├── index.html              # Trading Dashboard 主頁
├── nba.html                # NBA 預測頁面
├── mlb.html                # MLB 預測頁面
├── btc.html                # BTC 信號頁面
├── eth.html                # ETH 信號頁面
├── wti.html                # WTI 原油信號頁面
├── polymarket.html         # Polymarket 瀏覽器
│
├── MLB/                    # MLB 預測模組
│   └── mlb_predictor.py    # Elo + ERA + 牛棚 + 傷兵模型
│
├── pionex-bot/             # 核心後端
│   ├── dashboard.py        # Flask Web Server（所有 API 路由）
│   ├── nba_predictor.py    # NBA Elo + XGBoost 預測引擎
│   ├── client.py           # Pionex API 客戶端
│   ├── signal_manager.py   # 交易信號管理器
│   ├── portfolio_agent.py  # 投資組合管理 Agent
│   ├── evolution_agent.py  # 策略進化 Agent
│   ├── grid_strategy.py    # 網格交易策略
│   ├── triple_blade_bot.py # 三刀流交易機器人
│   ├── strategy.py         # Triple MA 策略
│   ├── config.py           # 設定管理
│   ├── utils.py            # 工具函式 + filelock
│   ├── logger.py           # 日誌系統
│   ├── notifier.py         # 通知系統
│   ├── risk.py             # 風控模組
│   ├── bots.toml           # Bot 設定檔
│   └── tests/              # 測試目錄
│
├── TripleCCI_Strategy_v5.pine  # TradingView Pine Script
├── ThreeBlade_Optimized.pine   # 三刀流 Pine Script
├── optimize-v5.js          # 參數最佳化腳本
├── backtest.js             # 回測引擎
└── .env.example            # 環境變數範本
```

## 核心模組說明

### 1. NBA 預測系統 (`pionex-bot/nba_predictor.py`)
- **模型**: Elo 評分 + XGBoost 機器學習
- **數據源**: ESPN Scoreboard + Standings + Injuries API
- **功能**:
  - Today's Games — 今日比賽預測 + 即時比分驗證
  - Next Games — 下一輪比賽預測（台灣運彩日期對齊）
  - 傷兵報告 — ESPN 即時傷兵數據，標示缺陣球星
  - 總分預測 — 預測總得分 + 誤差檢核
  - 回測績效 — 勝率、高信心命中率、歷史記錄
  - 今日/本周/本月勝率 KPI
- **勝率**: 回測約 65-70%

### 2. MLB 預測系統 (`MLB/mlb_predictor.py`)
- **模型**: Elo (35%) + 先發投手 ERA (25%) + 得失分差 (15%) + 牛棚 (10%) + 休息天 (10%) + 傷兵 (5%)
- **數據源**: ESPN MLB Scoreboard + Standings + Injuries API
- **功能**:
  - 先發投手 ERA 比較（含小樣本回歸修正）
  - 傷兵影響量化（明星球員 -4% / 一般球員 -2%）
  - 投手休息天懲罰（短修 ≤3 天 -3%）
  - 星等信心評級（★★★ = 60%+）
  - 今日勝率 + 60%+ 強推勝率
- **勝率**: 回測約 63-65%，強推 >58% 勝率 ~78%

### 3. 加密貨幣信號系統 (`btc.html`, `eth.html`, `wti.html`)
- **數據源**: Polymarket Gamma API（預測市場下注機率）
- **信號**: 方向偏差 + 區間分佈 + 突破機率 三維加權
- **自動刷新**: 30 秒更新

### 4. 交易機器人 (`pionex-bot/`)
- **Pionex 網格交易**: 自動建立/取消期貨網格 Bot
- **信號管理**: TripleCCI 策略驅動，自動翻多空
- **投資組合**: 自動資金分配 + 風控
- **策略進化**: 定期回測 + 自動淘汰劣勢 Bot

## API 端點一覽

| 端點 | 方法 | 說明 |
|------|------|------|
| `/api/nba/predictions` | GET | NBA 預測 JSON（today + next + backtest） |
| `/api/nba/scoreboard` | GET | ESPN NBA 即時比分代理 |
| `/api/mlb/predictions` | GET | MLB 預測 JSON |
| `/api/poly/markets` | GET | Polymarket 市場代理 |
| `/api/poly/events` | GET | Polymarket 事件代理 |
| `/api/tickers` | GET | Pionex PERP 行情 |
| `/api/klines` | GET | Pionex K 線數據 |
| `/api/bots` | GET | Bot 設定 |
| `/api/pnl` | GET | Bot P&L |
| `/api/state` | GET | Bot 狀態 |
| `/api/stream` | GET | SSE 即時行情推送 |

## 技術棧

| 層級 | 技術 |
|------|------|
| 前端 | 純 HTML/CSS/JS（無框架，單檔部署） |
| 後端 | Python Flask + httpx |
| 預測 | Elo 評分 + XGBoost + 統計模型 |
| 數據 | ESPN API + Polymarket API + Pionex API |
| 交易 | Pionex Bot API（期貨網格） |
| 策略 | TradingView Pine Script + Python Signal |
| 部署 | 本地 / Railway / Heroku |

## 常見問題

### Q: 啟動後頁面空白？
A: 等 5-10 秒讓 API 拉取數據。MLB 頁面首次載入約 15 秒。

### Q: NBA 預測沒有資料？
A: NBA 休賽期（6-10 月）無比賽資料。賽季期間自動有資料。

### Q: 如何只跑預測不跑交易？
A: 只啟動 `dashboard.py` 即可。交易功能需要另外啟動 `signal_manager.py` 並設定 Pionex API Key。

### Q: 如何修改 Bot 設定？
A: 編輯 `pionex-bot/bots.toml`，每個 `[bots.xxx]` 區塊是一個 Bot。

### Q: 如何部署到雲端？
A: 專案已包含 `Procfile` 和 `runtime.txt`，可直接部署到 Railway 或 Heroku。

## 學習路徑建議

1. **初學者**: 先看 `dashboard.py` 了解 Flask 路由 → 看 HTML 頁面了解前端渲染
2. **進階**: 看 `nba_predictor.py` 的 Elo 系統和預測引擎
3. **交易**: 看 `signal_manager.py` + `client.py` 了解自動交易流程
4. **策略**: 看 Pine Script 和 `optimize-v5.js` 了解策略開發和回測
