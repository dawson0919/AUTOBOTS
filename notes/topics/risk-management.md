---
title: 風險管理
tags: [topic, risk, position-sizing]
---

# 🛡️ 風險管理

## 💰 當前曝險彙總

### Pionex 機器人
| 群組 | 數量 | 保證金 | 名目（5×）|
|------|------|--------|----------|
| MA-Cross (bots.toml) | 9 | $465 | $2,325 |
| Q-SIGNALS (bots_qsignals.toml) | 6 | $300 | $1,500 |
| **總計** | **15** | **$765** | **$3,825** |

> Pionex 帳戶 free USDT：~$801 / frozen $30

### 主要在跑（截至 2026-04-15）
- BTC: 兩個 bot（MA-Cross + Q-SIGNALS）都做多
- ETH: 兩個 bot 都做多
- PAXG: Q-SIGNALS 做空，MA-Cross 做多 ⚠️ 對沖中
- USOX: Q-SIGNALS 做空，MA-Cross 做空 ✅

## 🎲 串關風險（NBA Predictor）

### 數學現實
| 單注命中率 | 2 關 | 3 關 |
|-----------|------|------|
| 55% | 30.3% | 16.6% |
| 60% | 36.0% | 21.6% |
| 65% | 42.3% | 27.5% |
| 70% | 49.0% | 34.3% |

### Kelly 倉位（建議）
```
stake = bankroll × 0.25 × edge / (odds - 1)
```
0.25 Kelly = 1/4 Kelly，避免過度槓桿。

## 🚨 監控指標

- 翻轉頻率（過度交易 → 高手續費）
- 最大回撤（單 bot / 總體）
- 同 symbol 多 bot 對沖（保證金浪費）
- Pionex 餘額 < $100 警報

## 🔗 相關
- [[topics/trading-strategies]]
- [[projects/pionex-bot]]
