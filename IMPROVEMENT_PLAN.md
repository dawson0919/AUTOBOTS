# 三刀流自動交易系統 — 改善計畫

## 一、系統審計摘要

| 維度 | 評分 (1-10) | 說明 |
|------|:-----------:|------|
| **程式碼品質** | 6 | 結構清晰但有重複程式碼；TOML解析器複製4次；缺乏型別檢查和單元測試 |
| **策略正確性** | 8 | signal_v6.py 與回測邏輯一致；flip-only 狀態機設計合理 |
| **風險管理** | 5 | 有基本防護但缺乏關鍵保護：無止損、無最大虧損強制平倉 |
| **系統架構** | 6 | 多代理設計概念佳，但代理間通訊僅靠 JSON 檔案，無鎖定機制 |
| **效能** | 7 | replay_signal_state 有 O(n*period) 問題，但 9 個 bot 仍可接受 |
| **缺失功能** | 4 | 缺乏通知系統、自動重啟、系統健康監控、秘密管理 |

**總體健康度: 6/10** — 作為 MVP 可運作，但距離生產級系統仍有明顯差距。

---

## 二、關鍵問題 (Critical Issues)

### BUG-1: Bot 取消後未重建 — 靜默停止交易
- **檔案**: signal_manager.py
- **問題**: XAUT 和 SLVX 的 bot_status = "canceled"，系統沒有建立新 bot 也沒有警告
- **修正**: 增加「孤兒 bot」偵測邏輯，如果 bot 已取消但有信號方向，應嘗試重建

### BUG-2: 狀態檔案競態條件 (Race Condition)
- **問題**: signal_manager 和 portfolio_agent 同時讀寫 state/*.json 和 bots.toml，無檔案鎖定
- **修正**: 使用 `filelock` 套件

### BUG-3: Dashboard 無認證暴露所有 bot 狀態
- **問題**: 綁定 0.0.0.0:5000，任何同網段裝置可存取
- **修正**: 加入 token 認證或只綁定 localhost

### BUG-4: 無硬止損機制
- **問題**: 5x 槓桿下 20% 不利波動 = 100% 保證金損失，系統無提前干預
- **修正**: 每個 bot 設定硬止損價格

---

## 三、重要改善 (High Priority)

### IMP-1: 建立共用模組 utils.py
- 統一 load_toml(), logging setup, BotAPIClient
- 消除 4 個檔案的 TOML 解析器重複

### IMP-2: Portfolio Agent 分配金額校正
- TOTAL_CAPITAL = 300 但實際部署 1640 USDT
- 應從帳戶餘額或 bots.toml 加總自動計算

### IMP-3: Telegram/LINE 通知系統
- flip 事件、錯誤、每日摘要
- bot 異常停止警告

### IMP-4: 前端動態化
- bot cards 從 /api/bots 動態生成
- 不再需要同時改 HTML + TOML

### IMP-5: 自動重啟/看門狗
- PM2 或 systemd service
- heartbeat 檔案機制

---

## 四、效能優化 (Performance)

### PERF-1: replay_signal_state 改用 cumsum SMA
- 效能提升 ~50x

### PERF-2: K 線請求快取
- 同一 symbol 同一 cycle 只請求一次

### PERF-3: 前端 K 線請求並行化
- Promise.all() 取代 await 串行

### PERF-4: 回測優化器並行化
- multiprocessing.Pool 加速 2500+ 組合

---

## 五、功能增強 (Feature Enhancement)

### FEAT-1: 網格範圍自動調整
### FEAT-2: 資金費率監控與分配權重
### FEAT-3: 多時間框架確認 (4H + 1H)
### FEAT-4: 動態相關性計算
### FEAT-5: 回測與實盤績效比較

---

## 六、長期路線圖 (Roadmap)

### Phase 1 (Week 1-2): 緊急修復
- [ ] 修復 BUG-1: 孤兒 bot 偵測與自動重建
- [ ] 修復 BUG-2: 狀態檔案加鎖 (filelock)
- [ ] 校正 TOTAL_CAPITAL 與實際部署金額
- [ ] 加入 Telegram 通知
- [ ] Dashboard 認證
- [ ] 建立 utils.py 共用模組

### Phase 2 (Week 3-4): 穩定性
- [ ] BotAPIClient 每 cycle 重新初始化
- [ ] PM2/systemd 看門狗
- [ ] 前端動態生成 bot cards
- [ ] K 線請求並行化
- [ ] signal_v6.py replay 改用 cumsum SMA
- [ ] 加入單元測試

### Phase 3 (Month 2): 進階功能
- [ ] 網格範圍自動調整
- [ ] 資金費率監控
- [ ] 多時間框架確認
- [ ] 回測 vs 實盤績效 dashboard
- [ ] evolution_agent 完整上線
- [ ] Dashboard 改用 FastAPI + WebSocket

### Phase 4 (Month 3+): 規模化
- [ ] 支援 15+ symbols (API rate limit)
- [ ] 動態相關性計算
- [ ] 策略 A/B 測試 (v6 vs v7)
- [ ] 自動回測排程
- [ ] 多交易所支援
- [ ] 備份/恢復流程

---

## 七、風險評估

| 風險 | 機率 | 影響 | 緩解措施 |
|------|:----:|:----:|----------|
| Bot 被外部取消但系統不知道 | 高 | 高 | BUG-1 修復 + 每 cycle 檢查 |
| Pionex API 長時間不可用 | 中 | 高 | 保守策略（保持現有方向） |
| 5x 槓桿下黑天鵝事件 | 低 | 極高 | 硬止損機制 |
| 代理同時寫入狀態檔案 | 中 | 中 | BUG-2 修復（檔案鎖定） |
| API key 洩露 | 中 | 高 | localhost + auth |
| 回測過擬合 | 高 | 中 | out-of-sample 驗證 |
| USOX MA 參數過於接近 | 高 | 低 | 確保 LB > 2*GY |

---

*Generated: 2026-04-03 | System: Autobots Three Kingdoms v6*
