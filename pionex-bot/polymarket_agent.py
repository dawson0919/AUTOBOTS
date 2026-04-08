"""
Polymarket Analysis Agent — Prediction Market Intelligence
==========================================================
Fetches trending markets, event analysis, and sentiment data from Polymarket.
No API key needed for public endpoints.

Usage:
    python polymarket_agent.py                    # Show trending markets
    python polymarket_agent.py --search "Trump"   # Search markets
    python polymarket_agent.py --event 12345      # Event details
    python polymarket_agent.py --hot               # Hot markets by volume
    python polymarket_agent.py --category politics # Filter by category
    python polymarket_agent.py --report            # Full daily report
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

import httpx

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


class PolymarketClient:
    """Public API client for Polymarket (no auth needed)."""

    def __init__(self):
        self.http = httpx.Client(timeout=15, follow_redirects=True)

    def close(self):
        self.http.close()

    # ── Gamma API: Markets & Events ──

    def get_markets(self, limit=20, offset=0, order="volume24hr",
                    ascending=False, active=True, closed=False, **filters):
        """List markets with sorting and filtering."""
        params = {
            "limit": limit, "offset": offset, "order": order,
            "ascending": str(ascending).lower(),
            "active": str(active).lower(), "closed": str(closed).lower(),
        }
        params.update(filters)
        r = self.http.get(f"{GAMMA}/markets", params=params)
        return r.json() if r.status_code == 200 else []

    def get_market(self, market_id):
        """Get single market by condition_id or slug."""
        r = self.http.get(f"{GAMMA}/markets/{market_id}")
        return r.json() if r.status_code == 200 else None

    def search_markets(self, query, limit=20):
        """Search markets by keyword."""
        r = self.http.get(f"{GAMMA}/markets", params={
            "tag": query, "limit": limit, "active": "true",
        })
        results = r.json() if r.status_code == 200 else []
        if not results:
            # Fallback: search in event titles
            r = self.http.get(f"{GAMMA}/events", params={
                "limit": limit, "active": "true",
            })
            events = r.json() if r.status_code == 200 else []
            q_lower = query.lower()
            results = [e for e in events if q_lower in str(e).lower()]
        return results

    def get_events(self, limit=20, offset=0, active=True, **filters):
        """List events."""
        params = {"limit": limit, "offset": offset, "active": str(active).lower()}
        params.update(filters)
        r = self.http.get(f"{GAMMA}/events", params=params)
        return r.json() if r.status_code == 200 else []

    def get_event(self, event_id):
        """Get single event with all markets."""
        r = self.http.get(f"{GAMMA}/events/{event_id}")
        return r.json() if r.status_code == 200 else None

    # ── CLOB API: Prices & Orderbook ──

    def get_midpoint(self, token_id):
        """Get midpoint price for a token."""
        r = self.http.get(f"{CLOB}/midpoint/{token_id}")
        return r.json() if r.status_code == 200 else None

    def get_orderbook(self, token_id):
        """Get orderbook for a token."""
        r = self.http.get(f"{CLOB}/order-book/{token_id}")
        return r.json() if r.status_code == 200 else None

    def get_price_history(self, token_id, interval="1d", fidelity=60):
        """Get price history for a token."""
        r = self.http.get(f"{CLOB}/prices-history", params={
            "market": token_id, "interval": interval, "fidelity": fidelity,
        })
        return r.json() if r.status_code == 200 else None

    def get_spread(self, token_id):
        """Get bid-ask spread."""
        r = self.http.get(f"{CLOB}/spread/{token_id}")
        return r.json() if r.status_code == 200 else None

    # ── Data API: Activity & Positions ──

    def get_market_trades(self, condition_id, limit=20):
        """Get recent trades for a market."""
        r = self.http.get(f"{DATA}/activity", params={
            "market": condition_id, "limit": limit,
        })
        return r.json() if r.status_code == 200 else []


# ── Analysis Functions ──

def format_market(m, idx=0):
    """Format a single market for display."""
    question = m.get("question", m.get("title", "?"))
    volume = float(m.get("volume", 0) or 0)
    volume_24h = float(m.get("volume24hr", 0) or 0)
    liquidity = float(m.get("liquidity", 0) or 0)

    # Extract outcome prices
    outcomes = m.get("outcomePrices", m.get("outcomes", ""))
    if isinstance(outcomes, str) and outcomes:
        try:
            outcomes = json.loads(outcomes)
        except:
            outcomes = []

    yes_price = float(outcomes[0]) * 100 if outcomes and len(outcomes) > 0 else 0
    no_price = float(outcomes[1]) * 100 if outcomes and len(outcomes) > 1 else 0

    end_date = m.get("endDate", m.get("end_date_iso", ""))
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            days_left = (end_dt - datetime.now(timezone.utc)).days
            end_str = f"{days_left}d left"
        except:
            end_str = end_date[:10]
    else:
        end_str = "ongoing"

    prefix = f"  {idx:2d}. " if idx > 0 else "  "

    lines = []
    lines.append(f"{prefix}{question}")
    lines.append(f"      YES: {yes_price:.0f}%  |  NO: {no_price:.0f}%  |  {end_str}")
    lines.append(f"      Vol: ${volume:,.0f}  |  24h: ${volume_24h:,.0f}  |  Liq: ${liquidity:,.0f}")
    return "\n".join(lines)


def show_trending(client, limit=15):
    """Show trending markets by 24h volume."""
    print("=" * 70)
    print("  POLYMARKET — Trending Markets (by 24h Volume)")
    print("=" * 70)

    markets = client.get_markets(limit=limit, order="volume24hr", ascending=False)
    if not markets:
        print("  No markets found")
        return

    for i, m in enumerate(markets, 1):
        print(format_market(m, i))
        print()


def show_hot(client, limit=15):
    """Show hot markets by total volume."""
    print("=" * 70)
    print("  POLYMARKET — Hot Markets (by Total Volume)")
    print("=" * 70)

    markets = client.get_markets(limit=limit, order="volume", ascending=False)
    for i, m in enumerate(markets, 1):
        print(format_market(m, i))
        print()


def search(client, query, limit=15):
    """Search markets by keyword."""
    print("=" * 70)
    print(f"  POLYMARKET — Search: '{query}'")
    print("=" * 70)

    markets = client.search_markets(query, limit=limit)
    if not markets:
        print("  No results found")
        return

    for i, m in enumerate(markets, 1):
        print(format_market(m, i))
        print()


def show_event(client, event_id):
    """Show detailed event info."""
    event = client.get_event(event_id)
    if not event:
        print(f"  Event {event_id} not found")
        return

    print("=" * 70)
    print(f"  EVENT: {event.get('title', '?')}")
    print("=" * 70)
    print(f"  Category: {event.get('category', '?')}")
    print(f"  Volume: ${float(event.get('volume', 0)):,.0f}")
    print(f"  Liquidity: ${float(event.get('liquidity', 0)):,.0f}")
    print()

    markets = event.get("markets", [])
    if markets:
        print(f"  Markets ({len(markets)}):")
        for i, m in enumerate(markets, 1):
            print(format_market(m, i))
            print()


def daily_report(client):
    """Generate comprehensive daily report."""
    print("=" * 70)
    print(f"  POLYMARKET DAILY REPORT — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 70)

    # Top by volume
    print("\n--- TOP 10 BY 24H VOLUME ---\n")
    markets = client.get_markets(limit=10, order="volume24hr", ascending=False)
    for i, m in enumerate(markets, 1):
        print(format_market(m, i))
        print()

    # Categories
    print("\n--- CATEGORY BREAKDOWN ---\n")
    all_markets = client.get_markets(limit=100, order="volume24hr", ascending=False)
    cats = {}
    for m in all_markets:
        # Try to extract category from tags
        tags = m.get("tags", [])
        cat = tags[0].get("label", "Other") if tags and isinstance(tags, list) and tags else "Other"
        if cat not in cats:
            cats[cat] = {"count": 0, "volume": 0}
        cats[cat]["count"] += 1
        cats[cat]["volume"] += float(m.get("volume24hr", 0) or 0)

    for cat, data in sorted(cats.items(), key=lambda x: x[1]["volume"], reverse=True)[:10]:
        print(f"  {cat:<25} {data['count']:3d} markets  ${data['volume']:>12,.0f} 24h vol")

    # High-conviction markets (>85% or <15%)
    print("\n--- HIGH CONVICTION (>85% YES or NO) ---\n")
    for m in all_markets:
        outcomes = m.get("outcomePrices", "")
        if isinstance(outcomes, str) and outcomes:
            try:
                outcomes = json.loads(outcomes)
            except:
                continue
        if outcomes and len(outcomes) >= 2:
            yes = float(outcomes[0]) * 100
            if yes > 85 or yes < 15:
                question = m.get("question", "?")[:60]
                vol = float(m.get("volume24hr", 0) or 0)
                print(f"  {yes:5.1f}% YES  ${vol:>10,.0f}  {question}")


# ── Main ──

def main():
    parser = argparse.ArgumentParser(description="Polymarket Analysis Agent")
    parser.add_argument("--trending", action="store_true", default=True, help="Show trending markets")
    parser.add_argument("--hot", action="store_true", help="Show hot markets by total volume")
    parser.add_argument("--search", type=str, help="Search markets by keyword")
    parser.add_argument("--event", type=str, help="Show event details by ID")
    parser.add_argument("--report", action="store_true", help="Full daily report")
    parser.add_argument("--limit", type=int, default=15, help="Number of results")
    args = parser.parse_args()

    client = PolymarketClient()

    try:
        if args.search:
            search(client, args.search, args.limit)
        elif args.event:
            show_event(client, args.event)
        elif args.hot:
            show_hot(client, args.limit)
        elif args.report:
            daily_report(client)
        else:
            show_trending(client, args.limit)
    finally:
        client.close()


if __name__ == "__main__":
    main()
