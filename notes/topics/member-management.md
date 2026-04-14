---
title: NBA Predictor 會員管理
tags: [topic, members, mitrade, workflow]
---

# 👥 NBA Predictor 會員管理流程

## 📊 統計（截至 2026-04-15）
- 總會員：115 人
- 已核可：57
- 已退件：47
- Pending：11

## 🔑 MITRADE 帳號驗證規則
- ✅ MITRADE 帳號必須為 **7 碼數字**
- ✅ Pionex 帳號必須為 **8 碼數字**
- ✅ 必須透過活動連結註冊：https://mytd.cc/py0h
- ✅ 必須填寫推廣碼：**OKoS**

## 🚫 標準退件理由
```
您的帳號已被拒絕申請，請透過活動連結註冊，
並在 MITRADE 註冊時填寫推廣碼：OKoS，
完成後再重新申請！
活動連結：https://mytd.cc/py0h
```

## 🛠️ 批次操作腳本

### 找指定 MITRADE 帳號的會員
```python
import sys, re; sys.path.insert(0,'.')
from models.user import list_users
TARGETS = {'5132544', '5194476'}
rx = re.compile(r'Mitrade[:：]\s*([^\s|/,;]+)', re.I)
for u in list_users():
    m = rx.search(u.get('trading_account', ''))
    if m and m.group(1) in TARGETS:
        print(u['id'], u['email'], u['status'])
```

### 批次退件
```python
from models.user import reject_user
reject_user(user_id, admin_id=1, reason='非本活動連結註冊請重新註冊')
```

## 🏆 VIP 名單
| 層級 | MITRADE | 會員 | 狀態 |
|------|---------|------|------|
| 超級大戶 | 1126502 | 未註冊 | — |
| 大戶 | 5212999 | yanwolf@gmail.com (#6) | rejected |
| 大戶 | 5347919 | pig22827253@yahoo.com.tw (#148) | pending |

## 📧 已寄信
- 51 名退件會員：說明退件理由 + 重新註冊步驟（BCC）
- 57 名核可會員：邀請加入 LINE 社群「刀派 Mitrade 平台海期討論群」
- 2 名大戶：誠摯邀請使用活動連結重新註冊

## 🔗 相關
- [[projects/nba-predictor]]
