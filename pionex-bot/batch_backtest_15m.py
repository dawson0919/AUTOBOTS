import os
import subprocess

# Config
SYMBOLS = ["BTC_USDT_PERP", "ETH_USDT_PERP", "SOL_USDT_PERP"]
INTERVAL = "15M"
LEVERAGE = "5"
COMMISSION = "0.0005"  # 0.05%
SLIPPAGE = "0.0005"    # 0.05%

def run_backtest(symbol):
    print(f"\n>>> Running Backtest: {symbol} @ {INTERVAL} (Lev {LEVERAGE}x) <<<")
    env = os.environ.copy()
    env["BT_SYMBOL"] = symbol
    env["BT_INTERVAL"] = INTERVAL
    env["BT_LEV"] = LEVERAGE
    env["BT_COMM"] = COMMISSION
    env["BT_SLIP"] = SLIPPAGE
    
    # Specific MA params based on bots.toml reference
    if "SOL" in symbol:
        env["BT_LB"] = "250"
        env["BT_GY"] = "50"
        env["BT_ZF"] = "10"
    else:
        env["BT_LB"] = "200"
        env["BT_GY"] = "50"
        env["BT_ZF"] = "20"

    subprocess.run(["python", "backtest_three_kingdoms.py"], env=env)

if __name__ == "__main__":
    for s in SYMBOLS:
        run_backtest(s)
    print("\nAll backtests complete.")
