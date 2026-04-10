"""
Auto-fetch NBA odds from playsport.cc
=====================================
Fetches Taiwan Sports Lottery odds and writes to nba_odds.json.

Usage:
    python fetch_odds.py              # Fetch and save
    python fetch_odds.py --show       # Fetch and print only
    python fetch_odds.py --daemon     # Run every 30 minutes

Can also be called from dashboard via /api/nba/odds/fetch
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

ODDS_FILE = Path(__file__).parent / "nba_odds.json"
PLAYSPORT_URL = "https://www.playsport.cc/predict/games?allianceid=3"

# Chinese team name → English full name mapping
TEAM_MAP = {
    "活塞": "Detroit Pistons", "黃蜂": "Charlotte Hornets",
    "熱火": "Miami Heat", "巫師": "Washington Wizards",
    "騎士": "Cleveland Cavaliers", "老鷹": "Atlanta Hawks",
    "76人": "Philadelphia 76ers", "乘76人": "Philadelphia 76ers",
    "溜馬": "Indiana Pacers",
    "暴龍": "Toronto Raptors", "尼克": "New York Knicks",
    "鵜鶘": "New Orleans Pelicans",
    "塞爾蒂克": "Boston Celtics", "塞爾提克": "Boston Celtics",
    "魔術": "Orlando Magic", "公牛": "Chicago Bulls",
    "獨行俠": "Dallas Mavericks", "馬刺": "San Antonio Spurs",
    "籃網": "Brooklyn Nets", "公鹿": "Milwaukee Bucks",
    "雷霆": "Oklahoma City Thunder", "金塊": "Denver Nuggets",
    "灰熊": "Memphis Grizzlies", "爵士": "Utah Jazz",
    "乘灰狼": "Minnesota Timberwolves", "灰狼": "Minnesota Timberwolves",
    "火箭": "Houston Rockets",
    "勇士": "Golden State Warriors", "國王": "Sacramento Kings",
    "快艇": "LA Clippers", "拓荒者": "Portland Trail Blazers",
    "太陽": "Phoenix Suns", "湖人": "Los Angeles Lakers",
    "乘公鹿": "Milwaukee Bucks", "乘火箭": "Houston Rockets",
    "乘金塊": "Denver Nuggets", "乘老鷹": "Atlanta Hawks",
    "乘馬刺": "San Antonio Spurs", "乘爵士": "Utah Jazz",
    "乘國王": "Sacramento Kings", "乘拓荒者": "Portland Trail Blazers",
    "乘湖人": "Los Angeles Lakers", "乘黃蜂": "Charlotte Hornets",
    "乘巫師": "Washington Wizards", "乘公牛": "Chicago Bulls",
    "乘溜馬": "Indiana Pacers", "乘尼克": "New York Knicks",
    "乘塞爾蒂克": "Boston Celtics", "乘塞爾提克": "Boston Celtics",
    "乘籃網": "Brooklyn Nets", "乘快艇": "LA Clippers",
}

# English abbreviation → full name
ABBR_MAP = {
    "DET": "Detroit Pistons", "CHA": "Charlotte Hornets",
    "MIA": "Miami Heat", "WAS": "Washington Wizards",
    "CLE": "Cleveland Cavaliers", "ATL": "Atlanta Hawks",
    "PHI": "Philadelphia 76ers", "IND": "Indiana Pacers",
    "TOR": "Toronto Raptors", "NYK": "New York Knicks",
    "NOP": "New Orleans Pelicans", "BOS": "Boston Celtics",
    "ORL": "Orlando Magic", "CHI": "Chicago Bulls",
    "DAL": "Dallas Mavericks", "SAS": "San Antonio Spurs",
    "BKN": "Brooklyn Nets", "MIL": "Milwaukee Bucks",
    "OKC": "Oklahoma City Thunder", "DEN": "Denver Nuggets",
    "MEM": "Memphis Grizzlies", "UTA": "Utah Jazz",
    "MIN": "Minnesota Timberwolves", "HOU": "Houston Rockets",
    "GSW": "Golden State Warriors", "SAC": "Sacramento Kings",
    "LAC": "LA Clippers", "POR": "Portland Trail Blazers",
    "PHX": "Phoenix Suns", "LAL": "Los Angeles Lakers",
}


def fetch_from_playsport() -> dict[str, dict]:
    """Fetch odds from playsport.cc HTML page."""
    try:
        r = httpx.get(PLAYSPORT_URL, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        })
        if r.status_code != 200:
            print(f"  [warn] playsport returned {r.status_code}")
            return {}
        return parse_playsport_html(r.text)
    except Exception as e:
        print(f"  [error] fetch failed: {e}")
        return {}


def parse_playsport_html(html: str) -> dict[str, dict]:
    """Parse playsport.cc HTML for NBA odds."""
    odds = {}

    # Try to find game data in script tags or structured content
    # playsport embeds data in Vue.js components

    # Pattern 1: Look for spread numbers near team names
    # Find all float numbers that look like spreads (±X.5)
    spread_pattern = re.findall(r'([+-]?\d+\.5)', html)
    ou_pattern = re.findall(r'(\d{3}\.5)', html)  # 200+ .5 numbers = O/U

    # Pattern 2: Try JSON data embedded in page
    json_matches = re.findall(r'var\s+\w+\s*=\s*(\{.*?\});', html, re.DOTALL)
    for jm in json_matches:
        try:
            data = json.loads(jm)
            # Look for game data structures
            if isinstance(data, dict) and any(k in str(data) for k in ['spread', 'handicap', 'total']):
                print(f"  Found JSON data with {len(data)} keys")
        except (json.JSONDecodeError, ValueError):
            pass

    # Pattern 3: Look for team names in Chinese
    for cn_name, en_name in TEAM_MAP.items():
        if cn_name in html:
            # Find nearby spread values
            idx = html.find(cn_name)
            context = html[max(0, idx-200):idx+200]
            spreads = re.findall(r'([+-]?\d+\.5)', context)
            ous = re.findall(r'(2\d{2}\.5)', context)
            if spreads or ous:
                print(f"  Found {en_name}: spreads={spreads[:3]} ou={ous[:2]}")

    return odds


def fetch_from_espn_games() -> list[str]:
    """Get today's NBA game matchups from our own API."""
    try:
        r = httpx.get("http://localhost:5000/api/nba/predictions", timeout=60)
        if r.status_code != 200:
            return []
        data = r.json()
        games = data.get("games", []) + data.get("next_games", [])
        return [f'{g["away"]} @ {g["home"]}' for g in games]
    except Exception:
        return []


def save_odds(odds: dict[str, dict]):
    """Save odds to nba_odds.json."""
    existing = {}
    if ODDS_FILE.exists():
        try:
            with open(ODDS_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass

    existing.setdefault("odds", {}).update(odds)
    existing["_last_fetch"] = datetime.now().isoformat()

    with open(ODDS_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)

    print(f"  Saved {len(odds)} odds to {ODDS_FILE}")


def interactive_input():
    """Prompt user to input odds for each game."""
    games = fetch_from_espn_games()
    if not games:
        print("  No games found. Is dashboard running?")
        return

    print(f"\n  Found {len(games)} games. Enter odds for each (or Enter to skip):\n")
    odds = {}
    for game in games:
        print(f"  {game}")
        try:
            spread_input = input(f"    主隊讓分 (e.g. -5.5, Enter=skip): ").strip()
            if not spread_input:
                continue
            ou_input = input(f"    大小分 O/U (e.g. 226.5): ").strip()
            odds[game] = {
                "spread": float(spread_input),
                "ou": float(ou_input) if ou_input else 0,
                "updated": datetime.now().isoformat(),
            }
            print(f"    ✅ Saved")
        except (ValueError, EOFError):
            continue

    if odds:
        save_odds(odds)
    print(f"\n  Done: {len(odds)} games updated")


def daemon_mode(interval: int = 1800):
    """Run fetch every N seconds."""
    print(f"  Daemon mode: fetching every {interval}s")
    while True:
        print(f"\n  [{datetime.now().strftime('%H:%M:%S')}] Fetching odds...")
        result = fetch_from_playsport()
        if result:
            save_odds(result)
        else:
            print("  No odds parsed (playsport may need JS rendering)")
            print("  Tip: Use 'python fetch_odds.py --input' for manual entry")
        time.sleep(interval)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    if "--show" in sys.argv:
        result = fetch_from_playsport()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif "--daemon" in sys.argv:
        daemon_mode()
    elif "--input" in sys.argv:
        interactive_input()
    else:
        # Default: try auto-fetch, if empty show instructions
        result = fetch_from_playsport()
        if result:
            save_odds(result)
        else:
            print("\n  ⚠️ 玩運彩需要 JS 渲染，無法直接抓取")
            print("  推薦方式：")
            print("    1. 請 AI 助手「抓盤口」→ 自動用 WebFetch 抓取並寫入")
            print("    2. python fetch_odds.py --input → 手動輸入")
            print("    3. 在 /nba 頁面底部表單輸入")
            print("    4. curl -X POST localhost:5000/api/nba/odds → API 批量寫入")
