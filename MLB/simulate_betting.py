"""
MLB 投注模擬器 — 計算不同策略下的資金增長
"""

import random
import json
import math

# ── 參數設定 ─────────────────────────────────────────────────────────────────

WIN_RATE = 0.624        # 回測勝率
ODDS = -110             # 美式賠率（需下注$110贏$100）
DECIMAL_ODDS = 1 + 100 / abs(ODDS)  # 小數賠率 = 1.9091
B = DECIMAL_ODDS - 1    # 淨賠率 = 0.9091
NUM_GAMES = 1000        # 模擬場次
SIMULATIONS = 10000     # 模擬次數（蒙地卡羅）
INITIAL_BANKROLL = 1000 # 初始本金

# ── 凱利準則計算 ─────────────────────────────────────────────────────────────

def kelly_fraction(p, b):
    """計算凱利公式最佳投注比例"""
    q = 1 - p
    return (b * p - q) / b

FULL_KELLY = kelly_fraction(WIN_RATE, B)
HALF_KELLY = FULL_KELLY / 2
QUARTER_KELLY = FULL_KELLY / 4

print(f"{'='*70}")
print(f"  MLB 投注模擬 — 本金 ${INITIAL_BANKROLL} / {NUM_GAMES}場")
print(f"{'='*70}")
print(f"  參數：")
print(f"    勝率       = {WIN_RATE*100:.1f}%")
print(f"    賠率       = {ODDS} (十進制 {DECIMAL_ODDS:.3f})")
print(f"    全凱利比例 = {FULL_KELLY*100:.2f}%")
print(f"    半凱利     = {HALF_KELLY*100:.2f}%")
print(f"    1/4凱利    = {QUARTER_KELLY*100:.2f}%")
print()

# ── 投注策略 ─────────────────────────────────────────────────────────────────

def simulate_flat_bet(bankroll, num_games, bet_amount, win_rate):
    """平注投注（每場固定金額）"""
    current = bankroll
    wins = 0
    for _ in range(num_games):
        if random.random() < win_rate:
            current += bet_amount * B
            wins += 1
        else:
            current -= bet_amount
        if current <= 0:
            return 0, wins
    return current, wins

def simulate_percentage_bet(bankroll, num_games, pct, win_rate):
    """固定比例投注（複利）"""
    current = bankroll
    wins = 0
    for _ in range(num_games):
        bet = current * pct
        if random.random() < win_rate:
            current += bet * B
            wins += 1
        else:
            current -= bet
        if current <= 0.01:
            return 0, wins
    return current, wins

def simulate_kelly(bankroll, num_games, kelly_pct, win_rate):
    """凱利投注（每場調整）"""
    current = bankroll
    wins = 0
    for _ in range(num_games):
        bet = current * kelly_pct
        if random.random() < win_rate:
            current += bet * B
            wins += 1
        else:
            current -= bet
        if current <= 0.01:
            return 0, wins
    return current, wins

def simulate_tiered(bankroll, num_games, win_rate):
    """分級投注（根據信心度）"""
    current = bankroll
    wins = 0
    for _ in range(num_games):
        # 模擬信心度分布：28.9%高信心(>58%), 71.1%一般
        if random.random() < 0.289:
            # 高信心：勝率76.2%，投注2%
            bet_pct = 0.02
            game_wr = 0.762
        else:
            # 一般：勝率58%，投注1%
            bet_pct = 0.01
            game_wr = 0.58
        
        bet = current * bet_pct
        if random.random() < game_wr:
            current += bet * B
            wins += 1
        else:
            current -= bet
        if current <= 0.01:
            return 0, wins
    return current, wins

# ── 蒙地卡羅模擬 ─────────────────────────────────────────────────────────────

def run_monte_carlo(strategy_func, *args, num_simulations=SIMULATIONS):
    """執行蒙地卡羅模擬"""
    results = []
    busts = 0
    for _ in range(num_simulations):
        final, wins = strategy_func(*args)
        results.append(final)
        if final <= 0:
            busts += 1
    return results, busts

print(f"  執行蒙地卡羅模擬 ({SIMULATIONS} 次)...")
print(f"{'='*70}\n")

strategies = []

# 策略1：平注 $10 (1%本金)
print(f"  [1] 平注投注 — 每場 $10 (1%本金)")
r, busts = run_monte_carlo(simulate_flat_bet, INITIAL_BANKROLL, NUM_GAMES, 10, WIN_RATE)
strategies.append(("平注 $10", r, busts))

# 策略2：平注 $20 (2%本金)
print(f"  [2] 平注投注 — 每場 $20 (2%本金)")
r, busts = run_monte_carlo(simulate_flat_bet, INITIAL_BANKROLL, NUM_GAMES, 20, WIN_RATE)
strategies.append(("平注 $20", r, busts))

# 策略3：1% 複利
print(f"  [3] 固定比例 — 1% (複利)")
r, busts = run_monte_carlo(simulate_percentage_bet, INITIAL_BANKROLL, NUM_GAMES, 0.01, WIN_RATE)
strategies.append(("1% 複利", r, busts))

# 策略4：2% 複利
print(f"  [4] 固定比例 — 2% (複利)")
r, busts = run_monte_carlo(simulate_percentage_bet, INITIAL_BANKROLL, NUM_GAMES, 0.02, WIN_RATE)
strategies.append(("2% 複利", r, busts))

# 策略5：3% 複利
print(f"  [5] 固定比例 — 3% (複利)")
r, busts = run_monte_carlo(simulate_percentage_bet, INITIAL_BANKROLL, NUM_GAMES, 0.03, WIN_RATE)
strategies.append(("3% 複利", r, busts))

# 策略6：5% 複利
print(f"  [6] 固定比例 — 5% (複利)")
r, busts = run_monte_carlo(simulate_percentage_bet, INITIAL_BANKROLL, NUM_GAMES, 0.05, WIN_RATE)
strategies.append(("5% 複利", r, busts))

# 策略7：1/4 凱利
print(f"  [7] 1/4 凱利 ({QUARTER_KELLY*100:.1f}%)")
r, busts = run_monte_carlo(simulate_kelly, INITIAL_BANKROLL, NUM_GAMES, QUARTER_KELLY, WIN_RATE)
strategies.append((f"1/4凱利 {QUARTER_KELLY*100:.1f}%", r, busts))

# 策略8：半凱利
print(f"  [8] 半凱利 ({HALF_KELLY*100:.1f}%)")
r, busts = run_monte_carlo(simulate_kelly, INITIAL_BANKROLL, NUM_GAMES, HALF_KELLY, WIN_RATE)
strategies.append((f"半凱利 {HALF_KELLY*100:.1f}%", r, busts))

# 策略9：全凱利
print(f"  [9] 全凱利 ({FULL_KELLY*100:.1f}%)")
r, busts = run_monte_carlo(simulate_kelly, INITIAL_BANKROLL, NUM_GAMES, FULL_KELLY, WIN_RATE)
strategies.append((f"全凱利 {FULL_KELLY*100:.1f}%", r, busts))

# 策略10：分級投注
print(f"  [10] 分級投注 (高信心2%/低信心1%)")
r, busts = run_monte_carlo(simulate_tiered, INITIAL_BANKROLL, NUM_GAMES, WIN_RATE)
strategies.append(("分級投注", r, busts))

# ── 結果輸出 ─────────────────────────────────────────────────────────────────

def percentile(data, p):
    """計算百分位數"""
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p / 100)
    return sorted_data[min(idx, len(sorted_data)-1)]

print(f"\n{'='*70}")
print(f"  模擬結果總結")
print(f"{'='*70}\n")

print(f"  {'策略':<20} {'破產率':<8} {'中位數':<10} {'平均數':<10} {'10%分位':<10} {'90%分位':<10} {'最高':<10} {'最低':<8}")
print(f"  {'-'*88}")

for name, results, busts in strategies:
    bust_rate = busts / len(results) * 100
    median = percentile(results, 50)
    mean = sum(results) / len(results)
    p10 = percentile(results, 10)
    p90 = percentile(results, 90)
    max_val = max(results)
    min_val = min(results)
    
    print(f"  {name:<20} {bust_rate:<7.1f}% {median:<10.0f} {mean:<10.0f} {p10:<10.0f} {p90:<10.0f} {max_val:<10.0f} {min_val:<8.0f}")

# ── 最佳策略推薦 ─────────────────────────────────────────────────────────────

print(f"\n{'='*70}")
print(f"  🏆 策略推薦")
print(f"{'='*70}\n")

# 找出破產率 < 5% 且中位數最高的策略
safe_strategies = [(name, r, b) for name, r, b in strategies if b/len(r)*100 < 5]
if safe_strategies:
    best = max(safe_strategies, key=lambda x: percentile(x[1], 50))
    name, results, busts = best
    median = percentile(results, 50)
    mean = sum(results) / len(results)
    print(f"  ✅ 最佳安全策略：{name}")
    print(f"     中位數：${median:.0f} ({(median/INITIAL_BANKROLL-1)*100:.0f}%)")
    print(f"     平均數：${mean:.0f}")
    print(f"     破產率：{busts/len(results)*100:.1f}%")
    print()

# 期望值計算
print(f"{'='*70}")
print(f"  📊 期望值分析")
print(f"{'='*70}\n")

ev_per_bet = WIN_RATE * B - (1 - WIN_RATE) * 1
print(f"  每場期望值 (每$1投注)：${ev_per_bet:.4f}")
print(f"  1000場期望收益：${INITIAL_BANKROLL * ev_per_bet * 10:.0f} (若每場投1%)")
print(f"  期望倍數：{(1 + ev_per_bet * 0.01) ** NUM_GAMES:.2f}x (複利)")
