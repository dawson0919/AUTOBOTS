"""
Crypto Signal Engine — Polymarket-based trading signals for BTC/ETH/SOL
=======================================================================
Fetches markets from Polymarket, filters by coin, analyzes probabilities,
produces composite bull/bear signals with price targets.

Usage:
    python crypto_signal.py                 # BTC signal (default)
    python crypto_signal.py --coin eth      # ETH signal
    python crypto_signal.py --coin sol      # SOL signal
    python crypto_signal.py --coin eth --json  # JSON output
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import httpx
except ImportError:
    import urllib.request

    class _SimpleGet:
        """Minimal fallback when httpx is unavailable."""
        @staticmethod
        def get(url, *, timeout=15):
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return type("R", (), {"json": lambda s=resp: json.loads(s.read())})()

    httpx = type("httpx", (), {"get": _SimpleGet.get})()

GAMMA_API = "https://gamma-api.polymarket.com"

# ── Coin Configuration ────────────────────────────────────────────────────────

COIN_CONFIG = {
    "btc": {
        "name": "Bitcoin",
        "symbol": "BTC",
        "keywords": r"\b(btc|bitcoin)\b",
        "price_min": 10000,
        "icon": "₿",
    },
    "eth": {
        "name": "Ethereum",
        "symbol": "ETH",
        "keywords": r"\b(eth|ethereum|ether)\b",
        "price_min": 500,
        "icon": "Ξ",
    },
    "sol": {
        "name": "Solana",
        "symbol": "SOL",
        "keywords": r"\b(sol|solana)\b",
        "price_min": 10,
        "icon": "◎",
    },
}

EXCLUDE_KEYWORDS = re.compile(
    r"(etf|approval|regulation|sec |reserve|strategic|dominance|halving|merge|shanghai|beacon)",
    re.IGNORECASE,
)

PRICE_KEYWORDS = re.compile(
    r"(price|above|below|up|down|range|\$[\d,]+|↑|↓)", re.IGNORECASE
)

# ── Fetch ────────────────────────────────────────────────────────────────────

def fetch_crypto_markets(coin: str) -> list[dict]:
    """Fetch active coin-related markets from Polymarket Gamma API."""
    cfg = COIN_CONFIG[coin]
    url = f"{GAMMA_API}/markets?limit=100&active=true&order=volume24hr&ascending=false"
    resp = httpx.get(url, timeout=15)
    all_markets = resp.json()

    coin_keywords = re.compile(cfg["keywords"], re.IGNORECASE)
    price_min = cfg["price_min"]

    results = []
    for m in all_markets:
        q = m.get("question", "") or m.get("title", "")
        if not coin_keywords.search(q):
            continue
        if not PRICE_KEYWORDS.search(q):
            continue
        if EXCLUDE_KEYWORDS.search(q):
            continue
        results.append(m)
    return results


# ── Parse ────────────────────────────────────────────────────────────────────

def parse_market(m: dict, price_min: int = 10000) -> dict:
    """Classify a market into updown / range / above_below with metadata."""
    q = (m.get("question", "") or m.get("title", "")).lower()

    try:
        prices = [float(x) for x in json.loads(m.get("outcomePrices", "[]"))]
    except (json.JSONDecodeError, TypeError, ValueError):
        prices = []
    try:
        outcomes = json.loads(m.get("outcomes", "[]"))
    except (json.JSONDecodeError, TypeError):
        outcomes = []

    vol = float(m.get("volume", 0) or m.get("volumeNum", 0) or 0)
    yes_price = prices[0] if prices else 0.0
    no_price = prices[1] if len(prices) > 1 else 0.0

    mtype = "other"
    direction = None
    range_low = None
    range_high = None
    threshold = None
    ab_direction = None
    timeframe = None

    # Timeframe detection
    if "5 min" in q or "5min" in q:
        timeframe = "5m"
    elif "15 min" in q or "15min" in q:
        timeframe = "15m"
    elif "1 hour" in q or "1hr" in q:
        timeframe = "1h"
    elif "4 hour" in q or "4hr" in q:
        timeframe = "4h"
    elif "daily" in q or re.search(
        r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d+", q
    ):
        timeframe = "daily"
    elif "weekly" in q or "week" in q:
        timeframe = "weekly"
    elif "monthly" in q or "month" in q:
        timeframe = "monthly"

    # Type classification
    if "up or down" in q or "up/down" in q:
        mtype = "updown"
        first_outcome = (outcomes[0] if outcomes else "").lower()
        if first_outcome == "up":
            direction = "up" if yes_price >= 0.5 else "down"
        else:
            direction = "down" if yes_price >= 0.5 else "up"

    elif re.search(r"\d[\d,]*\s*-\s*\d[\d,]*", q):
        rm = re.search(r"([\d,]+)\s*-\s*([\d,]+)", q)
        if rm:
            lo = int(rm.group(1).replace(",", ""))
            hi = int(rm.group(2).replace(",", ""))
            if lo >= price_min and hi >= price_min:
                mtype = "range"
                range_low = lo
                range_high = hi

    elif any(kw in q for kw in ("above", "below", "↑", "↓")):
        mtype = "above_below"
        nm = re.search(r"([\d,]+)", q)
        if nm:
            val = int(nm.group(1).replace(",", ""))
            if val >= price_min:
                threshold = val
                ab_direction = "above" if ("above" in q or "↑" in q) else "below"

    return {
        "id": m.get("id", ""),
        "question": m.get("question", "") or m.get("title", ""),
        "type": mtype,
        "direction": direction,
        "rangeLow": range_low,
        "rangeHigh": range_high,
        "threshold": threshold,
        "abDirection": ab_direction,
        "timeframe": timeframe,
        "yesPrice": yes_price,
        "noPrice": no_price,
        "volume": vol,
        "outcomes": outcomes,
        "slug": m.get("slug", ""),
    }


# ── Signal ───────────────────────────────────────────────────────────────────

def derive_signal(markets: list[dict]) -> dict | None:
    """Compute composite bull/bear signal from parsed markets."""
    if not markets:
        return None

    up_down = [m for m in markets if m["type"] == "updown"]
    price_range = [m for m in markets if m["type"] == "range"]
    above_below = [m for m in markets if m["type"] == "above_below"]

    # Direction bias (volume-weighted)
    bull_score = bear_score = dir_weight = 0.0
    for m in up_down:
        w = math.log10(max(m["volume"], 1) + 1)
        if m["direction"] == "up":
            bull_score += m["yesPrice"] * w
        else:
            bear_score += m["yesPrice"] * w
        dir_weight += w
    dir_bias = (bull_score - bear_score) / dir_weight if dir_weight > 0 else 0.0

    # Price range distribution
    expected_price = None
    top_range = None
    range_confidence = 0.0
    if price_range:
        total_prob = weighted_mid = max_prob = 0.0
        for m in price_range:
            if m["rangeLow"] is not None and m["rangeHigh"] is not None:
                mid = (m["rangeLow"] + m["rangeHigh"]) / 2
                weighted_mid += mid * m["yesPrice"]
                total_prob += m["yesPrice"]
                if m["yesPrice"] > max_prob:
                    max_prob = m["yesPrice"]
                    top_range = {
                        "low": m["rangeLow"],
                        "high": m["rangeHigh"],
                        "prob": m["yesPrice"],
                    }
        if total_prob > 0:
            expected_price = weighted_mid / total_prob
            range_confidence = max_prob

    # Above/below breakthrough
    above_score = below_score = ab_weight = 0.0
    for m in above_below:
        w = math.log10(max(m["volume"], 1) + 1)
        if m["abDirection"] == "above":
            above_score += m["yesPrice"] * w
        else:
            below_score += m["yesPrice"] * w
        ab_weight += w
    ab_bias = (above_score - below_score) / ab_weight if ab_weight > 0 else 0.0

    # Range bias: positive if expected price above median range
    range_bias = 0.0
    if expected_price and price_range:
        lows = [m["rangeLow"] for m in price_range if m["rangeLow"] is not None]
        highs = [m["rangeHigh"] for m in price_range if m["rangeHigh"] is not None]
        if lows and highs:
            median_price = (min(lows) + max(highs)) / 2
            if median_price > 0:
                range_bias = max(-1, min(1, (expected_price - median_price) / median_price * 5))

    # Composite signal
    composite = 0.5 * dir_bias + 0.3 * range_bias + 0.2 * ab_bias
    composite = max(-1, min(1, composite))

    # Strength = average of non-zero component strengths
    components = [abs(dir_bias), abs(range_bias), abs(ab_bias)]
    nonzero = [c for c in components if c > 0.01]
    strength = sum(nonzero) / len(nonzero) if nonzero else 0.0
    strength = min(1.0, strength)

    # Label + action
    if composite > 0.3:
        label, action = "STRONG BULL", "積極做多"
    elif composite > 0.15:
        label, action = "BULL", "輕倉做多"
    elif composite < -0.3:
        label, action = "STRONG BEAR", "積極做空"
    elif composite < -0.15:
        label, action = "BEAR", "輕倉做空"
    else:
        label, action = "NEUTRAL", "觀望等待"

    total_volume = sum(m["volume"] for m in markets)

    return {
        "composite": round(composite, 4),
        "label": label,
        "strength": round(strength, 4),
        "action": action,
        "dirBias": round(dir_bias, 4),
        "rangeBias": round(range_bias, 4),
        "abBias": round(ab_bias, 4),
        "topRange": top_range,
        "expectedPrice": round(expected_price) if expected_price else None,
        "marketCount": len(markets),
        "upDownCount": len(up_down),
        "rangeCount": len(price_range),
        "abCount": len(above_below),
        "totalVolume": round(total_volume, 2),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Strategies ───────────────────────────────────────────────────────────────

def suggest_strategies(signal: dict) -> list[dict]:
    """Generate strategy suggestions based on composite score."""
    c = signal["composite"]
    strategies = []

    if c > 0.3:
        strategies.append({"name": "積極做多", "desc": "多方機率偏高", "risk": "低", "riskColor": "#22c55e"})
        strategies.append({"name": "買看漲期權", "desc": "槓桿放大", "risk": "中", "riskColor": "#f59e0b"})
    elif c > 0.15:
        strategies.append({"name": "輕倉做多", "desc": "溫和偏多", "risk": "低", "riskColor": "#22c55e"})
    elif c < -0.3:
        strategies.append({"name": "積極做空", "desc": "空方佔優", "risk": "中", "riskColor": "#f59e0b"})
        strategies.append({"name": "減持現貨", "desc": "降低敞口", "risk": "低", "riskColor": "#22c55e"})
    elif c < -0.15:
        strategies.append({"name": "輕倉做空", "desc": "溫和偏空", "risk": "低", "riskColor": "#22c55e"})
    else:
        strategies.append({"name": "區間操作", "desc": "高拋低吸", "risk": "低", "riskColor": "#22c55e"})
        strategies.append({"name": "觀望等待", "desc": "等方向明朗", "risk": "無", "riskColor": "#64748b"})

    if signal.get("topRange"):
        tr = signal["topRange"]
        strategies.append({
            "name": f"目標 ${tr['low']:,}-${tr['high']:,}",
            "desc": f"最高機率 ({tr['prob']*100:.1f}%)",
            "risk": "參考",
            "riskColor": "#8b5cf6",
        })

    return strategies


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Crypto Signal Engine — Polymarket")
    parser.add_argument("--coin", default="btc", choices=list(COIN_CONFIG.keys()),
                        help="Coin to analyze (default: btc)")
    parser.add_argument("--json", action="store_true", help="JSON output for dashboard")
    args = parser.parse_args()

    coin = args.coin.lower()
    cfg = COIN_CONFIG[coin]
    price_min = cfg["price_min"]

    raw = fetch_crypto_markets(coin)
    markets = [parse_market(m, price_min) for m in raw]
    markets = [m for m in markets if m["type"] != "other"]

    signal = derive_signal(markets)

    if args.json:
        distribution = [
            {"rangeLow": m["rangeLow"], "rangeHigh": m["rangeHigh"], "prob": m["yesPrice"]}
            for m in markets
            if m["type"] == "range" and m["rangeLow"] is not None
        ]
        distribution.sort(key=lambda x: x["rangeLow"])

        output = {
            "coin": coin,
            "coinName": cfg["name"],
            "coinSymbol": cfg["symbol"],
            "signal": signal,
            "markets": markets,
            "distribution": distribution,
            "strategies": suggest_strategies(signal) if signal else [],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        if not signal:
            print(f"No {cfg['symbol']} markets found on Polymarket.")
            sys.exit(1)

        sc = signal["composite"]
        arrow = "▲" if sc > 0.15 else ("▼" if sc < -0.15 else "─")
        icon = cfg["icon"]
        sym = cfg["symbol"]
        print(f"\n{'='*50}")
        print(f"  {icon} {sym} Signal — {signal['label']}  {arrow}")
        print(f"{'='*50}")
        print(f"  Composite:  {sc:+.2f}  ({signal['action']})")
        print(f"  Strength:   {signal['strength']*100:.0f}%")
        print(f"  Dir Bias:   {signal['dirBias']:+.3f}")
        print(f"  Range Bias: {signal['rangeBias']:+.3f}")
        print(f"  A/B Bias:   {signal['abBias']:+.3f}")
        if signal.get("expectedPrice"):
            print(f"  Expected:   ${signal['expectedPrice']:,.0f}")
        if signal.get("topRange"):
            tr = signal["topRange"]
            print(f"  Top Range:  ${tr['low']:,} - ${tr['high']:,} ({tr['prob']*100:.1f}%)")
        print(f"  Markets:    {signal['marketCount']} (↕{signal['upDownCount']} □{signal['rangeCount']} ⇅{signal['abCount']})")
        print(f"  Volume:     ${signal['totalVolume']:,.0f}")
        print(f"  Time:       {signal['timestamp']}")
        print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
