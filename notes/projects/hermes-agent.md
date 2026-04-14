---
title: Hermes Agent
tags: [project, ai-agent, messaging, live]
project: hermes-agent
status: live (telegram)
---

# 🪽 Hermes Agent

AI agent framework，整合 OpenRouter / OpenAI / Qwen 模型，支援 Telegram / Discord / WhatsApp 訊息平台。

## 🚀 啟動指令

### Gateway（Telegram bot 服務）
```bash
cd f:/AUTOBOTS/hermes-agent
PYTHONIOENCODING=utf-8 venv/Scripts/hermes.exe gateway run
```

### 互動聊天
```bash
venv/Scripts/hermes.exe chat
```

### 狀態檢查
```bash
PYTHONIOENCODING=utf-8 venv/Scripts/hermes.exe status
```

## ⚙️ 配置
- **預設模型**：minimax/minimax-m2.7（OpenRouter）
- **Auth 已設定**：OpenRouter API Key、Qwen OAuth
- **訊息平台**：Telegram 已設定 ✅、Discord/WhatsApp 未設定
- **`.env`**：`f:/AUTOBOTS/hermes-agent/.env`

## ⚠️ 已知問題
- Telegram polling 在 Windows 偶有 `WinError 87`（asyncio Proactor loop 相容性）
- 若有多個 gateway 進程同時跑會 conflict（getUpdates token 衝突）

## 🔧 維護
```bash
# 更新到最新版（落後 191 commits）
hermes update

# 重新登入過期的 Qwen token
hermes auth
```

## 🔗 相關
- [[topics/infrastructure]] — Hermes server 部署
