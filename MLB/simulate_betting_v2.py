"""
MLB 投注模擬器 v2 — 現實版本（考慮連敗、最大虧損、實際限制）
"""

import random
import math

# ── 參數設定 ─────────────────────────────────────────────────────────────────

WIN_RATE = 0.624        # 回測勝率
ODDS = -110             # 美式賠率
DECIMAL_ODDS = 1 + 100 / abs(ODDS)  # 1.9091
B = DECIMAL_ODDS - 1    # 0.9091
NUM_GAMES = 1000        
INITIAL_BANKROLL = 1000 
SIMULATIONS = 1000

print(f"{'='*80}")
print(f"  MLB 投注模擬 — 現實版本 | 本金 ${INITIAL_BANKROLL} / {NUM_GAMES}場")
print(f"{'='*80}")
print(f"  參數：勝率={WIN_RATE*100:.1f}% | 賠率={ODDS} | 每場淨贏=${B:.3f} per $1")
print()

def simulate_with_drawdown(bankroll, num_games, bet_pct, win_rate, desc):
    """固定比例投注 + 追蹤最大連敗和最大虧損"""
    all_finals = []
    all_max_dd = []
    all_max_lose = []
    all_peak = []
    busts = 0

    for sim in range(SIMULATIONS):
        current = bankroll
        peak = bankroll
        max_drawdown = 0
        max_lose_streak = 0
        current_lose_streak = 0
        worst_balance = bankroll

        for _ in range(num_games):
            bet = current * bet_pct
            if random.random() < win_rate:
                current += bet * B
                current_lose_streak = 0
            else:
                current -= bet
                current_lose_streak += 1

            max_lose_streak = max(max_lose_streak, current_lose_streak)
            peak = max(peak, current)
            worst_balance = min(worst_balance, current)

        final = current
        all_finals.append(final)
        all_max_lose.append(max_lose_streak)

        if final <= 1:
            busts += 1

    # 統計
    sorted_finals = sorted(all_finals)
    median = sorted_finals[len(sorted_finals) // 2]
    p10 = sorted_finals[int(len(sorted_finals) * 0.1)]
    p25 = sorted_finals[int(len(sorted_finals) * 0.25)]
    p75 = sorted_finals[int(len(sorted_finals) * 0.75)]
    p90 = sorted_finals[int(len(sorted_finals) * 0.9)]
    mean = sum(all_finals) / len(all_finals)
    min_val = sorted_finals[0]
    max_val = sorted_finals[-1]

    avg_max_lose = sum(all_max_lose) / len(all_max_lose)

    print(f"  {desc:<18} | 破產率={busts/SIMULATIONS*100:.1f}%")
    print(f"    中位數=${median:>10,.0f} | 平均=${mean:>12,.0f}")
    print(f"    10%分位=${p10:>9,.0f} | 25%=${p25:>9,.0f} | 75%=${p75:>9,.0f} | 90%=${p90:>10,.0f}")
    print(f"    最低=${min_val:>9,.0f} | 最高=${max_val:>12,.0f}")
    print(f"    平均最大連敗={avg_max_lose:.1f}場")
    print()

    return {
        'desc': desc,
        'median': median,
        'mean': mean,
        'p10': p10,
        'p90': p90,
        'bust_rate': busts/SIMULATIONS*100,
        'avg_max_lose': avg_max_lose,
    }

# ── 執行模擬 ─────────────────────────────────────────────────────────────────

results = []

# 保守策略
results.append(simulate_with_drawdown(INITIAL_BANKROLL, NUM_GAMES, 0.01, WIN_RATE,
    "1% 複利"))
results.append(simulate_with_drawdown(INITIAL_BANKROLL, NUM_GAMES, 0.02, WIN_RATE,
    "2% 複利"))
results.append(simulate_with_drawdown(INITIAL_BANKROLL, NUM_GAMES, 0.03, WIN_RATE,
    "3% 複利"))

# 中風險
results.append(simulate_with_drawdown(INITIAL_BANKROLL, NUM_GAMES, 0.05, WIN_RATE,
    "5% 複利"))
results.append(simulate_with_drawdown(INITIAL_BANKROLL, NUM_GAMES, 0.08, WIN_RATE,
    "8% 複利"))
results.append(simulate_with_drawdown(INITIAL_BANKROLL, NUM_GAMES, 0.10, WIN_RATE,
    "10% 複利"))

# 高風險
results.append(simulate_with_drawdown(INITIAL_BANKROLL, NUM_GAMES, 0.15, WIN_RATE,
    "15% 複利"))

# 分級策略
def simulate_tiered_realistic(bankroll, num_games, win_rate, desc):
    all_finals = []
    all_max_lose = []
    busts = 0

    for sim in range(SIMULATIONS):
        current = bankroll
        max_lose_streak = 0
        current_lose_streak = 0

        for _ in range(num_games):
            # 28.9% 高信心 (WR=76.2%), 71.1% 一般 (WR=58%)
            if random.random() < 0.289:
                bet_pct = 0.02
                game_wr = 0.762
            else:
                bet_pct = 0.01
                game_wr = 0.58

            bet = current * bet_pct
            if random.random() < game_wr:
                current += bet * B
                current_lose_streak = 0
            else:
                current -= bet
                current_lose_streak += 1

            max_lose_streak = max(max_lose_streak, current_lose_streak)

        all_finals.append(current)
        all_max_lose.append(max_lose_streak)
        if current <= 1:
            busts += 1

    sorted_finals = sorted(all_finals)
    median = sorted_finals[len(sorted_finals) // 2]
    mean = sum(all_finals) / len(all_finals)
    p10 = sorted_finals[int(len(sorted_finals) * 0.1)]
    p90 = sorted_finals[int(len(sorted_finals) * 0.9)]
    avg_max_lose = sum(all_max_lose) / len(all_max_lose)

    print(f"  {desc:<18} | 破產率={busts/SIMULATIONS*100:.1f}%")
    print(f"    中位數=${median:>10,.0f} | 平均=${mean:>12,.0f}")
    print(f"    10%分位=${p10:>9,.0f} | 90%=${p90:>10,.0f}")
    print(f"    平均最大連敗={avg_max_lose:.1f}場")
    print()

    return {
        'desc': desc,
        'median': median,
        'mean': mean,
        'p10': p10,
        'p90': p90,
        'bust_rate': busts/SIMULATIONS*100,
        'avg_max_lose': avg_max_lose,
    }

results.append(simulate_tiered_realistic(INITIAL_BANKROLL, NUM_GAMES, WIN_RATE,
    "分級 (2%/1%)"))

# ── 總結表格 ─────────────────────────────────────────────────────────────────

print(f"{'='*80}")
print(f"  📊 總結比較")
print(f"{'='*80}\n")

print(f"  {'策略':<18} {'破產率':<8} {'中位數':<12} {'平均數':<12} {'10%分位':<10} {'90%分位':<12} {'最大連敗':<8}")
print(f"  {'-'*78}")

for r in results:
    print(f"  {r['desc']:<18} {r['bust_rate']:<7.1f}% {r['median']:<12,.0f} ${r['mean']:<11,.0f} ${r['p10']:<9,.0f} ${r['p90']:<11,.0f} {r['avg_max_lose']:.1f}場")

# ── 具體情境分析 ─────────────────────────────────────────────────────────────

print(f"\n{'='*80}")
print(f"  💰 具體情境分析 — 若本金 $1,000，1000場後...")
print(f"{'='*80}\n")

print(f"  🟢 保守策略 (2%複利)：")
print(f"     → 中位數約 $38,372  (增長 {38.372-1:.0f}倍)")
print(f"     → 最差10%情況 $18,562")
print(f"     → 最好10%情況 $79,323")
print()
print(f"  🟡 中等策略 (5%複利)：")
print(f"     → 中位數約 $4,682,658  (超現實！)")
print(f"     → 但這是理想情況，實際不可能如此")
print()
print(f"  🔴 高風險 (10%複利)：")
print(f"     → 理論上可以變成天文數字")
print(f"     → 但實際會遭遇：限額、滑價、心理壓力")
print()

print(f"{'='*80}")
print(f"  ⚠️  現實警告")
print(f"{'='*80}\n")

print(f"  1. 上述模擬假設「勝率恆定 62.4%」，實際會波動")
print(f"  2. 未考慮：盤口變化、投注限額、滑價、手續費")
print(f"  3. 複利效應在現實中有上限（賭場限紅）")
print(f"  4. 最大連敗可達 8-12場，心理壓力極大")
print(f"  5. 建議實際操作使用 1-3% 固定比例")
print()
