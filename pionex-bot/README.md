# Pionex 自動交易機器人

基於 Pionex API 的永續合約均線交叉策略自動交易系統。

## 功能

- **均線交叉策略 (MA Cross)**：快線上穿慢線做多，下穿做空
- **風控模組**：止損、止盈、每日最大虧損限制
- **DRY RUN 模式**：不實際下單，僅模擬信號與日誌
- **WebSocket 支援**：即時行情與訂單推送（可選）

## 快速開始

```bash
# 1. 安裝依賴
pip install -r requirements.txt

# 2. 配置 API 金鑰
cp .env.example .env
# 編輯 .env 填入你的 PIONEX_API_KEY 和 PIONEX_API_SECRET

# 3. 啟動 (預設 DRY RUN 模式)
python bot.py
```

## 配置說明 (.env)

| 參數 | 預設值 | 說明 |
|------|--------|------|
| `PIONEX_API_KEY` | - | Pionex API Key |
| `PIONEX_API_SECRET` | - | Pionex API Secret |
| `SYMBOL` | BTC_USDT_PERP | 交易對 |
| `FAST_MA_PERIOD` | 7 | 快線週期 |
| `SLOW_MA_PERIOD` | 25 | 慢線週期 |
| `KLINE_INTERVAL` | 15M | K線週期 (1M/5M/15M/30M/60M/4H/8H/12H/1D) |
| `MAX_POSITION_SIZE` | 0.01 | 最大倉位 (基礎貨幣單位) |
| `STOP_LOSS_PCT` | 2.0 | 止損百分比 |
| `TAKE_PROFIT_PCT` | 4.0 | 止盈百分比 |
| `MAX_DAILY_LOSS_PCT` | 5.0 | 每日最大虧損百分比 |
| `DRY_RUN` | true | 模擬模式 (true=不下單) |

## 專案結構

```
pionex-bot/
├── bot.py          # 主程式入口，交易循環
├── client.py       # Pionex REST API 客戶端 (認證/簽名)
├── ws_client.py    # Pionex WebSocket 客戶端
├── strategy.py     # MA Cross 均線交叉策略
├── risk.py         # 風控：止損/止盈/倉位管理
├── config.py       # 配置載入 (.env)
├── logger.py       # 日誌系統
├── .env.example    # 環境變數範本
└── requirements.txt
```

## 交易邏輯

```
每個 K 線週期：
  1. 拉取 K 線數據
  2. 檢查止損/止盈 → 觸發則平倉
  3. 計算快/慢均線
  4. 快線上穿慢線 (Golden Cross) → 平空 → 開多
  5. 快線下穿慢線 (Death Cross) → 平多 → 開空
  6. 等待下一根 K 線
```

## 注意事項

- **務必先用 DRY_RUN=true 測試**，確認信號正確再開啟實盤
- API Key 需開啟交易權限並設定 IP 白名單
- 頻率限制：私有端點 10 req/s，超限會被封禁 60 秒
- 此系統為基礎框架，實盤前建議加入更完整的回測驗證
