---
title: AI Trading Claude Skills
tags: [project, claude-skill, stock-analysis]
project: ai-trading-claude
status: installed
---

# 📈 AI Trading Analyst (Claude Skills)

Claude Code skills + agents for stock research and analysis. **研究工具，非交易機器人，不下單。**

## 📦 已安裝
- 16 Skills + 5 Agents + PDF 產生腳本
- 路徑：`~/.claude/skills/` + `~/.claude/agents/`
- 來源：https://github.com/zubair-trabzada/ai-trading-claude

## 🚀 使用範例
```
/trade analyze AAPL          # 5 agent 並行完整分析
/trade-quick TSLA            # 60 秒快照
/trade-compare NVDA AMD      # 兩支對比
/trade-options SPY           # 選擇權策略
/trade-screen                # 選股篩選
/trade-risk BTC              # 風險 + Kelly 倉位計算
/trade-report-pdf AAPL       # 產 PDF 報告
```

## 🤖 5 個 Agents（並行執行）
1. **trade-technical** — 價格動作、指標、型態
2. **trade-fundamental** — 估值、成長、資產負債
3. **trade-sentiment** — 新聞、社群、分析師評等
4. **trade-risk** — 波動、回撤、相關性
5. **trade-thesis** — 投資主題、催化劑、進出場

## 🎯 Trade Score 計算
0-100 綜合分數，輸出 Strong Buy / Buy / Hold / Caution / Avoid 訊號。

## 🔗 相關
- [[topics/trading-strategies]] — 對照 AUTOBOTS 自有策略
