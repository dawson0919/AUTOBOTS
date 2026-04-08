"""Portfolio Management Agent — Auto-allocate capital across 8 bots."""
from __future__ import annotations

import argparse
import json
import logging
import io
import os
import sys
import time
from datetime import datetime, timezone

import httpx
import numpy as np

from utils import load_toml, BOT_DIR, STATE_DIR, TOML_PATH

PORTFOLIO_STATE = STATE_DIR / "portfolio.json"

TOTAL_CAPITAL = float(os.getenv("PORTFOLIO_CAPITAL", "500"))  # Will be overridden by actual portfolio value
MIN_ALLOC = 20.0       # minimum per bot
MAX_ALLOC = 100.0      # maximum per bot (33%)
MAX_WEIGHT = 0.30
MIN_WEIGHT = 0.05
REBALANCE_HOURS = 4

# Correlation groups — max 50% in one group
CORR_GROUPS = {
    "precious_metals": ["xaut", "paxg", "slvx"],
    "crypto": ["btc", "eth", "sol"],
    "us_equity": ["qqqx", "usox"],
}
MAX_GROUP_PCT = 0.50

# Risk limits
MAX_DD_PER_BOT = 0.15      # 15%
MAX_DD_PORTFOLIO = 0.20     # 20%

# ── Imports from signal_manager ────────────────────────────────────────────

try:
    from signal_manager import BotAPIClient
except ImportError:
    # Standalone fallback — BotAPIClient unavailable; live metrics disabled.
    BotAPIClient = None  # type: ignore[assignment,misc]


# ── Logging ────────────────────────────────────────────────────────────────

def setup_logger(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("portfolio")
    logger.setLevel(getattr(logging, level, logging.INFO))
    if not logger.handlers:
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        utf8_out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        ch = logging.StreamHandler(utf8_out)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

        fh = logging.FileHandler(BOT_DIR / "portfolio_agent.log", encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


log = setup_logger()


# ── Helpers ────────────────────────────────────────────────────────────────

def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Average True Range over *period* bars."""
    trs: list[float] = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if not trs:
        return 0.0
    if len(trs) < period:
        return sum(trs) / len(trs)
    return sum(trs[-period:]) / period


def fetch_volatility(client, symbol: str, period: int = 30) -> float:
    """Fetch 30 1H klines, compute ATR ratio (ATR / close)."""
    try:
        klines = client.get_klines(symbol, "60M", limit=period + 1)
        if len(klines) < 3:
            return 0.05  # default moderate volatility
        highs = [float(k["high"]) for k in klines]
        lows = [float(k["low"]) for k in klines]
        closes = [float(k["close"]) for k in klines]
        atr_val = atr(highs, lows, closes, period=min(14, len(closes) - 1))
        last_close = closes[-1]
        if last_close <= 0:
            return 0.05
        return atr_val / last_close
    except Exception as e:
        log.warning("fetch_volatility(%s) failed: %s — using default", symbol, e)
        return 0.05


# ── Metrics Collection ─────────────────────────────────────────────────────

def collect_bot_metrics(client, bot_configs: dict) -> dict:
    """For each bot, read state file and query Pionex Bot API for P&L data.

    Returns dict[bot_name] -> {margin_balance, init_investment, roi, ...}
    """
    metrics: dict[str, dict] = {}

    for bot_name, bot_cfg in bot_configs.items():
        state_path = STATE_DIR / f"{bot_name}.json"
        bu_order_id = bot_cfg.get("bu_order_id", "")

        # Try reading bu_order_id from state file (may be newer than toml)
        if state_path.exists():
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    st = json.load(f)
                state_id = st.get("bu_order_id", "")
                if state_id:
                    bu_order_id = state_id
            except Exception:
                pass

        if not bu_order_id:
            log.info("[%s] No bu_order_id — using default allocation", bot_name.upper())
            metrics[bot_name] = _default_metrics(bot_cfg)
            continue

        # Query Pionex Bot API
        try:
            data = client.bot_get(bu_order_id)
            if not data:
                log.warning("[%s] bot_get returned None", bot_name.upper())
                metrics[bot_name] = _default_metrics(bot_cfg)
                continue

            bu = data.get("buOrderData", {})
            margin_balance = float(bu.get("marginBalance", 0))
            init_investment = float(bu.get("initQuoteInvestment", 0)) or float(bot_cfg.get("investment", 125))
            total_realized = float(bu.get("totalRealizedProfit", 0))
            grid_profit = float(bu.get("gridProfit", 0))
            total_fee = float(bu.get("totalFee", 0))
            total_funding = float(bu.get("totalFundingFee", 0))
            total_volume = float(bu.get("totalVolume", 0))
            status = bu.get("status", "unknown")
            create_time = bu.get("createTime", "")
            position = bu.get("position", {})

            roi = (margin_balance - init_investment) / init_investment if init_investment > 0 else 0.0

            # Funding rate tracking
            funding_fee = float(bu.get("totalFundingFee", 0) or 0)
            funding_payment = float(bu.get("fundingFeePayment", 0) or 0)
            age_hours = (time.time() * 1000 - float(bu.get("createTime", 0) or 0)) / 3600000

            # Daily funding rate cost as % of investment
            if init_investment > 0 and age_hours > 24:
                daily_funding_pct = (funding_fee / init_investment) / (age_hours / 24) * 100
            else:
                daily_funding_pct = 0

            metrics[bot_name] = {
                "margin_balance": margin_balance,
                "init_investment": init_investment,
                "total_realized_profit": total_realized,
                "grid_profit": grid_profit,
                "total_fee": total_fee,
                "total_funding_fee": total_funding,
                "funding_payment": funding_payment,
                "daily_funding_pct": daily_funding_pct,
                "total_volume": total_volume,
                "position": position,
                "create_time": create_time,
                "status": status,
                "roi": roi,
                "symbol": bot_cfg.get("symbol", ""),
            }
            log.info(
                "[%s] margin=%.2f init=%.2f ROI=%.2f%% funding=%.3f%%/day status=%s",
                bot_name.upper(), margin_balance, init_investment, roi * 100, daily_funding_pct, status,
            )

        except Exception as e:
            log.warning("[%s] Failed to collect metrics: %s", bot_name.upper(), e)
            metrics[bot_name] = _default_metrics(bot_cfg)

    return metrics


def _default_metrics(bot_cfg: dict) -> dict:
    inv = float(bot_cfg.get("investment", 125))
    return {
        "margin_balance": inv,
        "init_investment": inv,
        "total_realized_profit": 0,
        "grid_profit": 0,
        "total_fee": 0,
        "total_funding_fee": 0,
        "total_volume": 0,
        "position": {},
        "create_time": "",
        "status": "unknown",
        "roi": 0.0,
        "symbol": bot_cfg.get("symbol", ""),
    }


# ── Allocation Engine ──────────────────────────────────────────────────────

def compute_allocations(
    metrics: dict,
    volatilities: dict,
    total_capital: float,
) -> dict:
    """Score each bot and compute target allocations.

    score = max(0.1, 1 + roi) * (1 / max(0.01, volatility)) * regime_mult
    regime_mult = 1.2 if recent performance is positive, else 0.8
    """
    scores: dict[str, float] = {}

    for bot_name, m in metrics.items():
        roi = m.get("roi", 0.0)
        vol = volatilities.get(bot_name, 0.05)

        # Regime multiplier — positive ROI bots get a boost
        regime_mult = 1.2 if roi > 0 else 0.8

        score = max(0.1, 1.0 + roi) * (1.0 / max(0.01, vol)) * regime_mult

        # Penalize high funding costs (>0.5% daily)
        funding_pct = m.get("daily_funding_pct", 0)
        if abs(funding_pct) > 0.5:
            score *= 0.8  # 20% penalty
            log.info("[%s] Funding fee penalty: %.2f%% daily", bot_name.upper(), funding_pct)

        scores[bot_name] = score

    total_score = sum(scores.values())
    if total_score <= 0:
        total_score = 1.0

    # Raw weights
    weights: dict[str, float] = {b: s / total_score for b, s in scores.items()}

    # Apply min/max bounds
    weights = _apply_bounds(weights)

    # Apply correlation group caps
    weights = _apply_group_caps(weights)

    # Final allocation
    allocations: dict[str, dict] = {}
    for bot_name, w in weights.items():
        target = round(w * total_capital, 2)
        target = max(MIN_ALLOC, min(MAX_ALLOC, target))
        allocations[bot_name] = {
            "target_investment": target,
            "weight": round(w, 4),
            "score": round(scores.get(bot_name, 0), 4),
            "roi": round(metrics[bot_name].get("roi", 0), 4),
            "drawdown": 0.0,
        }

    return allocations


def _apply_bounds(weights: dict[str, float]) -> dict[str, float]:
    """Clamp weights to [MIN_WEIGHT, MAX_WEIGHT] and redistribute excess."""
    clamped = {}
    excess = 0.0
    unclamped_keys = []

    for b, w in weights.items():
        if w > MAX_WEIGHT:
            clamped[b] = MAX_WEIGHT
            excess += w - MAX_WEIGHT
        elif w < MIN_WEIGHT:
            clamped[b] = MIN_WEIGHT
            excess -= MIN_WEIGHT - w
        else:
            clamped[b] = w
            unclamped_keys.append(b)

    # Redistribute excess proportionally among unclamped
    if unclamped_keys and abs(excess) > 1e-6:
        unclamped_total = sum(clamped[b] for b in unclamped_keys)
        if unclamped_total > 0:
            for b in unclamped_keys:
                clamped[b] += excess * (clamped[b] / unclamped_total)

    # Normalize to sum=1
    total = sum(clamped.values())
    if total > 0:
        clamped = {b: w / total for b, w in clamped.items()}

    return clamped


def _apply_group_caps(weights: dict[str, float]) -> dict[str, float]:
    """Ensure no correlation group exceeds MAX_GROUP_PCT."""
    result = dict(weights)

    for group_name, members in CORR_GROUPS.items():
        active = [b for b in members if b in result]
        if not active:
            continue
        group_total = sum(result[b] for b in active)
        if group_total <= MAX_GROUP_PCT:
            continue

        # Scale down group members proportionally
        scale = MAX_GROUP_PCT / group_total
        freed = 0.0
        for b in active:
            old = result[b]
            result[b] = old * scale
            freed += old - result[b]

        # Redistribute freed weight to non-group bots
        others = [b for b in result if b not in active]
        others_total = sum(result[b] for b in others)
        if others and others_total > 0:
            for b in others:
                result[b] += freed * (result[b] / others_total)

    # Re-normalize
    total = sum(result.values())
    if total > 0:
        result = {b: w / total for b, w in result.items()}

    return result


# ── Risk Management ────────────────────────────────────────────────────────

def check_risk(metrics: dict, portfolio_state: dict) -> dict:
    """Check per-bot and portfolio-level drawdown limits.

    Returns dict with 'halted' flag and 'warnings' list.
    """
    warnings: list[str] = []
    halted = False

    # Per-bot drawdown
    for bot_name, m in metrics.items():
        roi = m.get("roi", 0.0)
        if roi < -MAX_DD_PER_BOT:
            warnings.append(
                f"[{bot_name.upper()}] Drawdown {roi*100:.1f}% exceeds limit {MAX_DD_PER_BOT*100:.0f}%"
            )

    # Portfolio drawdown
    portfolio_value = sum(m.get("margin_balance", 0) for m in metrics.values())
    peak_value = max(portfolio_state.get("peak_value", TOTAL_CAPITAL), portfolio_value)
    if peak_value > 0:
        dd = (peak_value - portfolio_value) / peak_value
        if dd > MAX_DD_PORTFOLIO:
            halted = True
            warnings.append(
                f"PORTFOLIO drawdown {dd*100:.1f}% exceeds limit {MAX_DD_PORTFOLIO*100:.0f}% — HALTING"
            )

    for w in warnings:
        log.warning("RISK: %s", w)

    return {
        "halted": halted,
        "warnings": warnings,
        "portfolio_value": portfolio_value,
        "peak_value": peak_value,
    }


# ── State Persistence ──────────────────────────────────────────────────────

def load_portfolio_state() -> dict:
    """Load portfolio state from disk."""
    if PORTFOLIO_STATE.exists():
        with open(PORTFOLIO_STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "total_capital": TOTAL_CAPITAL,
        "last_rebalance": None,
        "portfolio_value": TOTAL_CAPITAL,
        "peak_value": TOTAL_CAPITAL,
        "max_drawdown_seen": 0.0,
        "halted": False,
        "allocations": {},
        "history": [],
    }


def save_portfolio_state(
    allocations: dict,
    metrics: dict,
    portfolio_value: float,
    peak_value: float,
    halted: bool,
    warnings: list[str],
):
    """Write state/portfolio.json with current allocations and history."""
    STATE_DIR.mkdir(exist_ok=True)

    old_state = load_portfolio_state()
    history = old_state.get("history", [])

    # Append history entry (keep last 100)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "portfolio_value": round(portfolio_value, 2),
        "allocations": {
            b: {"target": a["target_investment"], "weight": a["weight"]}
            for b, a in allocations.items()
        },
    }
    history.append(entry)
    history = history[-100:]

    dd_seen = old_state.get("max_drawdown_seen", 0.0)
    if peak_value > 0:
        current_dd = (peak_value - portfolio_value) / peak_value
        dd_seen = max(dd_seen, current_dd)

    state = {
        "total_capital": TOTAL_CAPITAL,
        "last_rebalance": datetime.now(timezone.utc).isoformat(),
        "portfolio_value": round(portfolio_value, 2),
        "peak_value": round(peak_value, 2),
        "max_drawdown_seen": round(dd_seen, 4),
        "halted": halted,
        "warnings": warnings,
        "allocations": allocations,
        "history": history,
    }

    with open(PORTFOLIO_STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    log.info("Portfolio state saved to %s", PORTFOLIO_STATE)


# ── Main Cycle ─────────────────────────────────────────────────────────────

def run_cycle(dry_run: bool = True):
    """Orchestrate: collect -> volatility -> risk check -> allocate -> save."""
    log.info("=" * 60)
    log.info("Portfolio Agent — Rebalance Cycle")
    log.info("=" * 60)

    # Load config
    cfg = load_toml(str(TOML_PATH))
    bot_configs = cfg.get("bots", {})

    if not bot_configs:
        log.error("No bots found in %s", TOML_PATH)
        return

    log.info("Bots: %s", ", ".join(bot_configs.keys()))

    # Load existing portfolio state
    pf_state = load_portfolio_state()

    if pf_state.get("halted"):
        log.warning("Portfolio is HALTED due to risk limits. Manual review required.")
        log.warning("To resume, set halted=false in %s", PORTFOLIO_STATE)
        return

    # Initialize API client
    if BotAPIClient is None:
        log.error("BotAPIClient not available — cannot collect live metrics")
        return

    client = BotAPIClient()

    try:
        # 1. Collect bot metrics
        log.info("--- Collecting bot metrics ---")
        metrics = collect_bot_metrics(client, bot_configs)

        # 2. Fetch volatility for each bot
        log.info("--- Fetching volatility ---")
        volatilities: dict[str, float] = {}
        for bot_name, bot_cfg in bot_configs.items():
            symbol = bot_cfg.get("symbol", "")
            vol = fetch_volatility(client, symbol)
            volatilities[bot_name] = vol
            log.info("[%s] volatility=%.4f", bot_name.upper(), vol)

        # 3. Risk check
        log.info("--- Risk check ---")
        risk = check_risk(metrics, pf_state)
        portfolio_value = risk["portfolio_value"]
        peak_value = risk["peak_value"]

        if risk["halted"]:
            log.warning("RISK HALT triggered — saving state and stopping")
            save_portfolio_state(
                pf_state.get("allocations", {}), metrics,
                portfolio_value, peak_value, True, risk["warnings"],
            )
            return

        # 4. Compute allocations
        log.info("--- Computing allocations ---")
        allocations = compute_allocations(metrics, volatilities, TOTAL_CAPITAL)

        # Print summary
        log.info("--- Allocation Summary ---")
        log.info("%-8s %8s %8s %8s %8s %10s", "BOT", "WEIGHT", "TARGET", "ROI", "SCORE", "FUND%/D")
        log.info("-" * 58)
        for bot_name in sorted(allocations.keys()):
            a = allocations[bot_name]
            fund_pct = metrics.get(bot_name, {}).get("daily_funding_pct", 0)
            log.info(
                "%-8s %7.1f%% %7.1f$ %7.1f%% %8.2f %9.3f%%",
                bot_name.upper(),
                a["weight"] * 100,
                a["target_investment"],
                a["roi"] * 100,
                a["score"],
                fund_pct,
            )
        total_alloc = sum(a["target_investment"] for a in allocations.values())
        log.info("-" * 45)
        log.info("Total allocated: %.2f / %.2f USDT", total_alloc, TOTAL_CAPITAL)
        log.info("Portfolio value:  %.2f USDT", portfolio_value)

        if dry_run:
            log.info("[DRY RUN] Allocations computed but NOT saved.")
            return

        # 5. Save
        save_portfolio_state(
            allocations, metrics,
            portfolio_value, peak_value,
            False, risk.get("warnings", []),
        )

    finally:
        client.close()

    log.info("Rebalance cycle complete.\n")


# ── Entry Point ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Portfolio Management Agent")
    parser.add_argument("--once", action="store_true", help="Run single cycle")
    parser.add_argument("--loop", action="store_true", help="Run every %dh" % REBALANCE_HOURS)
    parser.add_argument("--dry-run", action="store_true", default=True, help="Dry run (default)")
    parser.add_argument("--live", action="store_true", help="Enable live writes")
    parser.add_argument("--log-level", default="INFO", help="Log level")
    args = parser.parse_args()

    global log
    log = setup_logger(args.log_level)

    dry_run = not args.live

    if args.loop:
        log.info("Portfolio Agent starting in LOOP mode (every %dh)", REBALANCE_HOURS)
        log.info("Dry run: %s", dry_run)
        while True:
            try:
                run_cycle(dry_run=dry_run)
            except Exception as e:
                log.error("Cycle failed: %s", e, exc_info=True)
            log.info("Sleeping %dh until next cycle...", REBALANCE_HOURS)
            time.sleep(REBALANCE_HOURS * 3600)
    else:
        run_cycle(dry_run=dry_run)


if __name__ == "__main__":
    main()
