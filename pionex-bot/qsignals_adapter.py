"""Q-SIGNALS strategy adapter.

Thin Python wrapper around the Q-SIGNALS JS strategy runner. Lets signal_manager.py
(or any consumer) evaluate any of the 10 Q-SIGNALS strategies with full fidelity —
no Python re-implementation of the 436-line indicators library needed.

Prerequisite: Node.js on PATH. The JS engine is mirrored into
pionex-bot/strategies_qsignals/qsignals_src/.

Available strategy ids (matches runner.js REGISTRY):
    dual_st_breakout, donchian_trend, dual_ema, granville_eth_4h,
    ichimoku_cloud, ma60, macd_ma, mean_reversion, three_style, turtle_breakout
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

RUNNER = Path(__file__).parent / "strategies_qsignals" / "runner.js"

# Symbols routed per strategy based on Q-SIGNALS OPTIMIZED_PARAMS coverage.
# Expand as you calibrate more pairs.
STRATEGY_COVERAGE = {
    "dual_st_breakout":   ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XAUUSDT", "CLUSDT"],
    "donchian_trend":     ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "dual_ema":           ["BTCUSDT", "ETHUSDT"],
    "granville_eth_4h":   ["ETHUSDT"],
    "ichimoku_cloud":     ["BTCUSDT", "ETHUSDT"],
    "ma60":               ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "macd_ma":            ["BTCUSDT", "ETHUSDT"],
    "mean_reversion":     ["BTCUSDT", "XAUUSDT", "PAXGUSDT"],
    "three_style":        ["BTCUSDT", "ETHUSDT", "CLUSDT"],
    "turtle_breakout":    ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
}


def evaluate(
    strategy_id: str,
    symbol: str,
    timeframe: str,
    candles: Iterable[dict],
    params: dict | None = None,
    timeout: int = 20,
) -> dict:
    """Run a Q-SIGNALS strategy against `candles` and return its signal.

    candles items: {open, high, low, close, volume, time}

    Returns: {"signal": "BUY|SELL|CLOSE_LONG|CLOSE_SHORT|HOLD",
              "price": float, "strategy": str, "params_used": dict}
    """
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js not found on PATH; install from nodejs.org")

    payload = {
        "strategy": strategy_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "params": params or {},
        "candles": list(candles),
    }
    proc = subprocess.run(
        [node, str(RUNNER)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Q-SIGNALS runner failed: {proc.stderr}")
    return json.loads(proc.stdout)


def list_strategies() -> list[str]:
    return list(STRATEGY_COVERAGE.keys())


def strategies_for_symbol(symbol: str) -> list[str]:
    """Return strategy ids that have calibrated params for this symbol."""
    symbol_u = symbol.upper().replace("_USDT_PERP", "USDT").replace("-", "")
    return [sid for sid, syms in STRATEGY_COVERAGE.items() if symbol_u in syms]


if __name__ == "__main__":
    # Smoke test — synthetic candles
    import math
    fake = []
    for i in range(200):
        base = 100 + math.sin(i / 8) * 5 + i * 0.05
        fake.append({
            "open": base,
            "high": base + 0.5,
            "low": base - 0.5,
            "close": base + 0.1,
            "volume": 1000,
            "time": 1_700_000_000 + i * 3600,
        })
    for sid in ["dual_st_breakout", "mean_reversion", "donchian_trend"]:
        try:
            out = evaluate(sid, "BTCUSDT", "1h", fake)
            print(f"{sid:20s} → {out['signal']}  (price={out['price']:.2f})")
        except Exception as e:
            print(f"{sid:20s} → ERROR: {e}")
