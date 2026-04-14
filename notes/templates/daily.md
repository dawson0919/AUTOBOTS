<%*
const d = tp.date.now("YYYY-MM-DD")
const dow = tp.date.now("dddd")
-%>
---
title: <% d %> Daily
tags: [daily]
date: <% d %>
weekday: <% dow %>
---

# 📅 <% d %> (<% dow %>)

## 🌅 早晨計畫
-

## 🎯 今日完成
- [ ]

## 📊 系統狀態
- [ ] NBA Predictor http://localhost:8000
- [ ] Pionex Dashboard http://localhost:5000
- [ ] Q-SIGNALS Bot Manager loop
- [ ] Hermes Gateway

## 💹 交易紀錄
| Bot | 動作 | 價格 | 備註 |
|-----|------|------|------|

## 📝 學到 / 想到
-

## 📥 待辦（轉到明天 or 主題）
- [ ]

## 🔗 連結
- [[../_INDEX|← 首頁]]
- 昨天：[[<% tp.date.yesterday("YYYY-MM-DD") %>]]
- 明天：[[<% tp.date.tomorrow("YYYY-MM-DD") %>]]
