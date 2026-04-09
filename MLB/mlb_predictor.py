"""
MLB Predictor — Elo + Starter ERA + Run Differential Model
============================================================
Predicts MLB game outcomes using:
  1. Team Elo ratings (walk-forward updated)
  2. Probable starting pitcher ERA (major factor in MLB)
  3. Team run differential (RS - RA)
  4. Home field advantage
  5. ESPN injury report

Data source: ESPN MLB APIs (scoreboard, standings, injuries)

Usage:
    python mlb_predictor.py --json          # JSON output for dashboard
    python mlb_predictor.py --backtest      # Backtest vs historical games
    python mlb_predictor.py                 # Print today's predictions
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import time

# ── Constants ─────────────────────────────────────────────────────────────────

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb"
MLB_DIR = Path(__file__).parent
MODEL_PATH = MLB_DIR / "mlb_model.json"

# MLB home field advantage is smaller than NBA (~54% win rate)
HOME_ELO_ADV = 24  # ~54% implied
ELO_K = 6  # Lower K for 162-game season (less volatile)
ELO_MEAN = 1500


def _http():
    return httpx.Client(timeout=15, follow_redirects=True,
                        headers={"User-Agent": "MLBPredictor/1.0"})


# ── Elo System ────────────────────────────────────────────────────────────────

class EloSystem:
    def __init__(self, k: int = ELO_K):
        self.ratings: dict[str, float] = {}
        self.k = k

    def get(self, team: str) -> float:
        return self.ratings.setdefault(team, ELO_MEAN)

    def expected(self, team_a: str, team_b: str, home_adv: float = 0) -> float:
        ra = self.get(team_a) + home_adv
        rb = self.get(team_b)
        return 1 / (1 + 10 ** ((rb - ra) / 400))

    def update(self, winner: str, loser: str, home_team: str | None = None):
        ha = HOME_ELO_ADV if winner == home_team else (-HOME_ELO_ADV if loser == home_team else 0)
        exp_w = self.expected(winner, loser, ha)
        delta = self.k * (1 - exp_w)
        self.ratings[winner] = self.get(winner) + delta
        self.ratings[loser] = self.get(loser) - delta

    def to_dict(self):
        return dict(self.ratings)

    def from_dict(self, d):
        self.ratings = dict(d)


# ── Data Fetching ─────────────────────────────────────────────────────────────

def fetch_scoreboard(date_str: str | None = None) -> list[dict]:
    """Fetch MLB games from ESPN. date_str format: YYYYMMDD."""
    try:
        url = f"{ESPN_BASE}/scoreboard"
        if date_str:
            url += f"?dates={date_str}"
        with _http() as c:
            r = c.get(url)
            if r.status_code != 200:
                return []
            data = r.json()
    except Exception as exc:
        print(f"  [warn] ESPN MLB fetch failed: {exc}", file=sys.stderr)
        return []

    games = []
    for event in data.get("events", []):
        comp = event.get("competitions", [{}])[0]
        teams = comp.get("competitors", [])
        if len(teams) < 2:
            continue
        home = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
        away = next((t for t in teams if t.get("homeAway") == "away"), teams[1])

        # Probable starters
        def _get_starter(team_data):
            probs = team_data.get("probables", [])
            if not probs:
                return None
            p = probs[0]
            athlete = p.get("athlete", {})
            stats = {s["abbreviation"]: s["displayValue"] for s in p.get("statistics", [])}
            record = p.get("record", "")
            raw_era = float(stats.get("ERA", "4.50") or "4.50")
            wins = int(stats.get("W", "0") or "0")
            losses = int(stats.get("L", "0") or "0")
            decisions = wins + losses

            # Small sample ERA regression: blend toward league avg (4.20)
            # With 0-1 starts, mostly use league avg; with 5+ starts, trust ERA
            if decisions <= 1:
                adj_era = raw_era * 0.3 + 4.20 * 0.7  # 70% league avg
            elif decisions <= 3:
                adj_era = raw_era * 0.5 + 4.20 * 0.5  # 50/50
            elif decisions <= 5:
                adj_era = raw_era * 0.7 + 4.20 * 0.3  # 70% actual
            else:
                adj_era = raw_era  # Trust the sample

            return {
                "name": athlete.get("displayName", "TBD"),
                "era": round(adj_era, 2),
                "raw_era": raw_era,
                "wins": wins,
                "losses": losses,
                "record": record,
            }

        games.append({
            "home": home["team"]["displayName"],
            "away": away["team"]["displayName"],
            "home_abbr": home["team"].get("abbreviation", ""),
            "away_abbr": away["team"].get("abbreviation", ""),
            "home_record": home.get("records", [{}])[0].get("summary", ""),
            "away_record": away.get("records", [{}])[0].get("summary", ""),
            "home_score": int(home.get("score", 0) or 0),
            "away_score": int(away.get("score", 0) or 0),
            "home_starter": _get_starter(home),
            "away_starter": _get_starter(away),
            "status": event.get("status", {}).get("type", {}).get("description", ""),
            "date": event.get("date", ""),
            "winner": home["team"]["displayName"] if home.get("winner") else
                      away["team"]["displayName"] if away.get("winner") else "",
        })
    return games


def fetch_standings() -> dict[str, dict]:
    """Fetch MLB standings. Returns {team_name: {wins, losses, pct, rs, ra, diff}}."""
    try:
        with _http() as c:
            r = c.get(f"{ESPN_BASE.replace('/scoreboard','')}/standings")
            if r.status_code != 200:
                return {}
            data = r.json()
    except Exception:
        return {}

    result = {}
    for league in data.get("children", []):
        for entry in league.get("standings", {}).get("entries", []):
            team = entry.get("team", {}).get("displayName", "")
            stats = {s["name"]: s["displayValue"] for s in entry.get("stats", [])}
            w = int(stats.get("wins", 0) or 0)
            l = int(stats.get("losses", 0) or 0)
            rs = float(stats.get("pointsFor", 0) or 0)
            ra = float(stats.get("pointsAgainst", 0) or 0)
            gp = w + l or 1
            result[team] = {
                "wins": w, "losses": l,
                "pct": round(w / gp, 3),
                "rs": rs, "ra": ra,
                "rs_pg": round(rs / gp, 2),
                "ra_pg": round(ra / gp, 2),
                "diff": round((rs - ra) / gp, 2),
            }
    return result


def fetch_team_pitching() -> dict[str, dict]:
    """Fetch team-level pitching stats (ERA, WHIP) from ESPN team stats API.
    Returns {team_id: {era, whip, k9, ops_against}}."""
    # ESPN team IDs for all 30 MLB teams
    TEAM_IDS = {
        "1": "Baltimore Orioles", "2": "Boston Red Sox", "3": "New York Yankees",
        "4": "Tampa Bay Rays", "5": "Toronto Blue Jays",
        "6": "Chicago White Sox", "7": "Cleveland Guardians", "8": "Detroit Tigers",
        "9": "Kansas City Royals", "10": "Minnesota Twins",
        "11": "Houston Astros", "12": "Los Angeles Angels", "13": "Oakland Athletics",
        "14": "Seattle Mariners", "15": "Texas Rangers",
        "16": "Atlanta Braves", "17": "Miami Marlins", "18": "New York Mets",
        "19": "Philadelphia Phillies", "20": "Washington Nationals",
        "21": "Chicago Cubs", "22": "Cincinnati Reds", "23": "Milwaukee Brewers",
        "24": "Pittsburgh Pirates", "25": "St. Louis Cardinals",
        "26": "Arizona Diamondbacks", "27": "Colorado Rockies",
        "28": "Los Angeles Dodgers", "29": "San Diego Padres", "30": "San Francisco Giants",
    }
    result = {}
    try:
        with _http() as c:
            for tid, tname in TEAM_IDS.items():
                try:
                    r = c.get(f"{ESPN_BASE.replace('/scoreboard','')}/teams/{tid}/statistics",
                              timeout=8)
                    if r.status_code != 200:
                        continue
                    data = r.json()
                    # Find pitching category
                    results_data = data.get("results", data)
                    cats = []
                    if isinstance(results_data, dict):
                        cats = results_data.get("splits", {}).get("categories", [])
                        if not cats:
                            # Try alternative structure
                            for item in results_data.get("splits", []):
                                cats.extend(item.get("categories", []))
                    pitching = {}
                    for cat in cats:
                        if cat.get("name") == "pitching":
                            for s in cat.get("stats", []):
                                pitching[s["abbreviation"]] = s.get("value", 0)
                            break
                    if pitching:
                        result[tname] = {
                            "team_era": round(pitching.get("ERA", 4.50), 2),
                            "whip": round(pitching.get("WHIP", 1.30), 2),
                            "k9": round(pitching.get("K/9", 8.0), 1),
                            "ops_against": round(pitching.get("OOPS", 0.700), 3),
                        }
                except Exception:
                    continue
    except Exception:
        pass
    return result


def fetch_injuries() -> dict[str, list[dict]]:
    """Fetch MLB injury report. Returns {team_name: [{name, status, detail}]}."""
    try:
        with _http() as c:
            r = c.get(f"{ESPN_BASE.replace('/scoreboard','')}/injuries")
            if r.status_code != 200:
                return {}
            data = r.json()
    except Exception:
        return {}

    result = {}
    for team_data in data.get("injuries", []):
        team_name = team_data.get("displayName", "")
        injuries = []
        for inj in team_data.get("injuries", []):
            athlete = inj.get("athlete", {})
            injuries.append({
                "name": athlete.get("displayName", "Unknown"),
                "status": inj.get("status", ""),
                "detail": inj.get("shortComment", ""),
                "position": athlete.get("position", ""),
            })
        if injuries:
            result[team_name] = injuries
    return result


def fetch_results(days: int = 30) -> list[dict]:
    """Fetch historical results for Elo building."""
    all_games = []
    base = datetime.now()
    with _http() as c:
        for i in range(days):
            d = base - timedelta(days=i + 1)
            ds = d.strftime("%Y%m%d")
            try:
                r = c.get(f"{ESPN_BASE}/scoreboard?dates={ds}")
                if r.status_code != 200:
                    continue
                data = r.json()
            except Exception:
                continue

            for event in data.get("events", []):
                comp = event.get("competitions", [{}])[0]
                teams = comp.get("competitors", [])
                status = event.get("status", {}).get("type", {}).get("name", "")
                if status != "STATUS_FINAL" or len(teams) < 2:
                    continue
                home = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
                away = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
                hs = int(home.get("score", 0) or 0)
                aws = int(away.get("score", 0) or 0)
                if hs == aws:
                    continue  # Skip ties (shouldn't happen in MLB)
                winner = home["team"]["displayName"] if hs > aws else away["team"]["displayName"]

                # Get starter ERA from probables
                def _era(td):
                    probs = td.get("probables", [])
                    if probs:
                        for s in probs[0].get("statistics", []):
                            if s.get("abbreviation") == "ERA":
                                try:
                                    return float(s["displayValue"])
                                except (ValueError, KeyError):
                                    pass
                    return None

                all_games.append({
                    "date": ds,
                    "home": home["team"]["displayName"],
                    "away": away["team"]["displayName"],
                    "home_score": hs,
                    "away_score": aws,
                    "winner": winner,
                    "home_era": _era(home),
                    "away_era": _era(away),
                })
    return all_games


# ── Prediction Engine ─────────────────────────────────────────────────────────

class MLBPredictor:
    def __init__(self):
        self.elo = EloSystem()
        self.standings: dict[str, dict] = {}
        self.injury_map: dict[str, list[dict]] = {}
        self.team_pitching: dict[str, dict] = {}  # team ERA, WHIP, K/9

    # ── Key player detection ──
    # These are star-level players whose absence significantly impacts outcomes
    STAR_HITTERS = {
        "Shohei Ohtani", "Aaron Judge", "Mookie Betts", "Juan Soto", "Ronald Acuna Jr.",
        "Freddie Freeman", "Corey Seager", "Trea Turner", "Julio Rodriguez",
        "Corbin Carroll", "Yordan Alvarez", "Bobby Witt Jr.", "Gunnar Henderson",
        "Elly De La Cruz", "Fernando Tatis Jr.", "Vladimir Guerrero Jr.",
        "Bryce Harper", "Rafael Devers", "Marcus Semien", "Pete Alonso",
        "Wander Franco", "Adley Rutschman", "Matt Olson", "Kyle Tucker",
    }
    STAR_PITCHERS = {
        "Gerrit Cole", "Spencer Strider", "Corbin Burnes", "Zack Wheeler",
        "Shane McClanahan", "Yoshinobu Yamamoto", "Blake Snell", "Max Scherzer",
        "Justin Verlander", "Shohei Ohtani", "Logan Webb", "Framber Valdez",
        "Sonny Gray", "Chris Sale", "Tyler Glasnow", "Tarik Skubal",
        "Paul Skenes", "Dylan Cease", "Hunter Brown", "Luis Castillo",
    }

    def predict(self, home: str, away: str,
                home_era: float | None = None,
                away_era: float | None = None,
                home_rest: int | None = None,
                away_rest: int | None = None,
                home_injuries: list[dict] | None = None,
                away_injuries: list[dict] | None = None) -> float:
        """Predict home team win probability with full context."""
        # 1. Elo component (with home advantage)
        elo_exp = self.elo.expected(home, away, HOME_ELO_ADV)

        # 2. Starter ERA component — AMPLIFIED
        # League avg ERA ~4.20. Starting pitcher is 60%+ of game outcome in MLB
        era_adj = 0
        if home_era is not None and away_era is not None:
            era_diff = away_era - home_era  # positive = home pitcher better
            era_adj = era_diff * 0.06  # ~6% per 1.0 ERA difference (was 4%)
            era_adj = max(-0.20, min(0.20, era_adj))  # Cap at ±20% (was ±15%)

        # 3. Run differential component — AMPLIFIED
        h_stats = self.standings.get(home, {})
        a_stats = self.standings.get(away, {})
        h_diff = h_stats.get("diff", 0)
        a_diff = a_stats.get("diff", 0)
        diff_adj = 0
        if h_diff != 0 or a_diff != 0:
            diff_adj = (h_diff - a_diff) * 0.03  # ~3% per 1.0 (was 2%)
            diff_adj = max(-0.15, min(0.15, diff_adj))

        # 4. Pitcher rest day penalty
        # Short rest (3 days) → ERA typically inflates 0.5-1.0
        # Normal rest (4-5 days) → no adjustment
        rest_adj = 0
        if home_rest is not None and home_rest <= 3:
            rest_adj -= 0.03  # Home pitcher on short rest → away advantage
        if away_rest is not None and away_rest <= 3:
            rest_adj += 0.03  # Away pitcher on short rest → home advantage

        # 5. Team pitching (bullpen) quality
        # Starter pitches ~5-6 innings, bullpen pitches ~3-4 innings = ~40% of game
        # Team ERA reflects overall pitching staff including bullpen
        bullpen_adj = 0
        h_pitch = self.team_pitching.get(home, {})
        a_pitch = self.team_pitching.get(away, {})
        h_team_era = h_pitch.get("team_era", 4.20)
        a_team_era = a_pitch.get("team_era", 4.20)
        if h_team_era > 0 and a_team_era > 0:
            # Lower team ERA = better bullpen + overall pitching
            bullpen_diff = a_team_era - h_team_era  # positive = home pitching better
            bullpen_adj = bullpen_diff * 0.03  # 3% per 1.0 team ERA difference
            bullpen_adj = max(-0.10, min(0.10, bullpen_adj))

        # 6. Injury impact
        # Star hitter OUT = -3% win probability per star
        # Star pitcher OUT = -2% (less immediate unless it's the starter)
        injury_adj = 0
        if home_injuries:
            for p in home_injuries:
                if p.get("status") == "Out":
                    name = p.get("name", "")
                    if name in self.STAR_HITTERS:
                        injury_adj -= 0.04  # Lost star hitter = -4%
                    elif name in self.STAR_PITCHERS:
                        injury_adj -= 0.02  # Lost star pitcher (non-starter) = -2%
        if away_injuries:
            for p in away_injuries:
                if p.get("status") == "Out":
                    name = p.get("name", "")
                    if name in self.STAR_HITTERS:
                        injury_adj += 0.04  # Away lost star = home advantage
                    elif name in self.STAR_PITCHERS:
                        injury_adj += 0.02
        injury_adj = max(-0.12, min(0.12, injury_adj))  # Cap at ±12%

        # Composite: 35% Elo + 25% Starter ERA + 15% Run Diff + 10% Bullpen + 10% Rest + 5% Injury
        prob = (0.35 * elo_exp
                + 0.25 * (0.5 + era_adj)
                + 0.15 * (0.5 + diff_adj)
                + 0.10 * (0.5 + bullpen_adj)
                + 0.10 * (0.5 + rest_adj)
                + 0.05 * (0.5 + injury_adj))

        # Sharpen: push away from 0.5 to create more decisive predictions
        # Logistic sharpening: amplify edges while keeping calibration
        if prob != 0.5:
            edge = prob - 0.5
            sharpened = edge * 1.4  # 40% amplification
            prob = 0.5 + max(-0.45, min(0.45, sharpened))

        return max(0.05, min(0.95, prob))

    def predict_total(self, home: str, away: str,
                      home_era: float | None = None,
                      away_era: float | None = None) -> float:
        """Predict total runs."""
        h_stats = self.standings.get(home, {})
        a_stats = self.standings.get(away, {})
        h_rs = h_stats.get("rs_pg", 4.5)
        a_rs = a_stats.get("rs_pg", 4.5)
        h_ra = h_stats.get("ra_pg", 4.5)
        a_ra = a_stats.get("ra_pg", 4.5)

        # Expected runs: avg of team offense vs opponent defense
        away_runs = (a_rs + h_ra) / 2
        home_runs = (h_rs + a_ra) / 2

        # ERA adjustment: better pitcher suppresses runs
        if home_era is not None:
            era_factor = home_era / 4.20  # relative to league avg
            away_runs *= (0.5 + 0.5 * era_factor)  # home pitcher limits away runs
        if away_era is not None:
            era_factor = away_era / 4.20
            home_runs *= (0.5 + 0.5 * era_factor)

        return round(away_runs + home_runs, 1)

    def save(self):
        state = {
            "elo": self.elo.to_dict(),
            "saved_at": datetime.now().isoformat(),
        }
        MODEL_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def load(self) -> bool:
        if MODEL_PATH.exists():
            try:
                state = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
                self.elo.from_dict(state.get("elo", {}))
                return True
            except Exception:
                pass
        return False


# ── Build Elo from History ────────────────────────────────────────────────────

def build_elo(predictor: MLBPredictor, days: int = 30):
    """Fetch recent results and build Elo ratings."""
    print(f"  Fetching {days} days of MLB results...")
    games = fetch_results(days)
    games.sort(key=lambda g: g["date"])
    print(f"  Found {len(games)} completed games")

    for g in games:
        predictor.elo.update(g["winner"],
                             g["home"] if g["winner"] != g["home"] else g["away"],
                             g["home"])
    predictor.save()
    print(f"  Elo ratings built for {len(predictor.elo.ratings)} teams")


# ── Backtest ──────────────────────────────────────────────────────────────────

def cmd_backtest(predictor: MLBPredictor, days: int):
    """Walk-forward backtest."""
    print(f"\n  MLB Backtest — {days} days walk-forward")
    all_games = fetch_results(days)
    all_games.sort(key=lambda g: g["date"])

    if not all_games:
        print("  No data for backtesting.")
        return

    # Use first 1/3 as warmup
    warmup = len(all_games) // 3
    test_games = all_games[warmup:]
    bt_elo = EloSystem()

    # Warmup Elo
    for g in all_games[:warmup]:
        bt_elo.update(g["winner"],
                      g["home"] if g["winner"] != g["home"] else g["away"],
                      g["home"])

    total = correct = 0
    strong = strong_correct = 0
    vstrong = vstrong_correct = 0  # Very strong >65%
    home_picks = home_correct = 0
    away_picks = away_correct = 0
    results = []
    daily_results: dict[str, list] = {}  # date → [correct bools]

    for g in test_games:
        home, away = g["home"], g["away"]
        exp = bt_elo.expected(home, away, HOME_ELO_ADV)

        # ERA adjustment — amplified (same as predict())
        era_adj = 0
        if g.get("home_era") and g.get("away_era"):
            era_diff = g["away_era"] - g["home_era"]
            era_adj = era_diff * 0.06  # 6% per 1.0 ERA
            era_adj = max(-0.20, min(0.20, era_adj))

        prob = 0.45 * exp + 0.30 * (0.5 + era_adj) + 0.15 * 0.5 + 0.10 * 0.5

        # Sharpen (same as predict)
        if prob != 0.5:
            edge = prob - 0.5
            prob = 0.5 + max(-0.45, min(0.45, edge * 1.4))
        prob = max(0.05, min(0.95, prob))

        pick = home if prob > 0.5 else away
        conf = max(prob, 1 - prob)
        win = (pick == g["winner"])

        total += 1
        if win:
            correct += 1
        if conf > 0.58:
            strong += 1
            if win: strong_correct += 1
        if conf > 0.65:
            vstrong += 1
            if win: vstrong_correct += 1

        # Track home/away pick accuracy
        if pick == home:
            home_picks += 1
            if win: home_correct += 1
        else:
            away_picks += 1
            if win: away_correct += 1

        # Daily tracking
        daily_results.setdefault(g["date"], []).append(win)

        results.append({
            "date": g["date"],
            "away": away, "home": home,
            "conf": round(conf * 100),
            "pick": pick, "winner": g["winner"],
            "correct": win,
            "score": f'{g["away_score"]}-{g["home_score"]}',
            "home_era": g.get("home_era"),
            "away_era": g.get("away_era"),
        })

        # Update Elo
        bt_elo.update(g["winner"],
                      home if g["winner"] != home else away,
                      home)

    wr = round(correct / total * 100, 1) if total > 0 else 0
    swr = round(strong_correct / strong * 100, 1) if strong > 0 else 0
    vswr = round(vstrong_correct / vstrong * 100, 1) if vstrong > 0 else 0
    hwr = round(home_correct / home_picks * 100, 1) if home_picks > 0 else 0
    awr = round(away_correct / away_picks * 100, 1) if away_picks > 0 else 0

    # Calculate profit (flat bet $100 at -110 odds)
    profit = correct * (100 / 1.10) - (total - correct) * 100
    roi = round(profit / (total * 100) * 100, 1) if total > 0 else 0

    # Best/worst days
    best_day = max(daily_results.items(), key=lambda x: sum(x[1]) / len(x[1]) if x[1] else 0,
                   default=("", []))
    worst_day = min(daily_results.items(), key=lambda x: sum(x[1]) / len(x[1]) if x[1] else 1,
                    default=("", []))

    print(f"  Total: {total} games, WR: {wr}%")
    print(f"  Strong (>58%): {strong} games, WR: {swr}%")
    print(f"  Very Strong (>65%): {vstrong} games, WR: {vswr}%")
    print(f"  Home picks: {home_picks} ({hwr}%) | Away picks: {away_picks} ({awr}%)")
    print(f"  ROI (flat -110): {roi}%")
    print(f"  Last 10:")
    for r in results[-10:]:
        mark = "OK" if r["correct"] else "XX"
        print(f"    {r['date']} {r['away'][:12]:>12} @ {r['home'][:12]:<12} "
              f"pick={r['pick'][:10]:<10} {r['conf']}% [{mark}] {r['score']}")

    return {
        "games_tested": total,
        "all_wr": wr,
        "strong_count": strong,
        "strong_wr": swr,
        "vstrong_count": vstrong,
        "vstrong_wr": vswr,
        "home_picks": home_picks,
        "home_wr": hwr,
        "away_picks": away_picks,
        "away_wr": awr,
        "roi": roi,
        "best_day": best_day[0] if best_day[1] else "",
        "worst_day": worst_day[0] if worst_day[1] else "",
        "recent": results[-30:],
    }


# ── JSON Output for Dashboard ─────────────────────────────────────────────────

def cmd_json(predictor: MLBPredictor, days: int):
    """Generate JSON for dashboard."""
    # Suppress prints during build
    _real_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        build_elo(predictor, days)
    finally:
        sys.stdout = _real_stdout
        sys.stdout.reconfigure(encoding="utf-8")

    standings = fetch_standings()
    predictor.standings = standings
    injury_map = fetch_injuries()
    # Use RA/G from standings as bullpen proxy (fast, no extra API calls)
    # Lower RA/G = better overall pitching staff
    team_pitching = {}
    for tname, st in standings.items():
        ra_pg = st.get("ra_pg", 4.5)
        team_pitching[tname] = {"team_era": round(ra_pg * 0.9, 2)}  # RA/G ≈ ERA * 1.1
    predictor.team_pitching = team_pitching

    output = {"games": [], "next_games": [], "next_games_date": "",
              "standings_top": [], "backtest": None}

    # Today's games
    _et = timezone(timedelta(hours=-4))
    et_today = datetime.now(_et).strftime("%Y%m%d")
    today_games = fetch_scoreboard() + fetch_scoreboard(et_today)
    # Deduplicate
    seen = set()
    deduped = []
    for g in today_games:
        key = g["home"] + "|" + g["away"]
        if key not in seen:
            seen.add(key)
            deduped.append(g)
    today_games = deduped

    # Split by Taiwan date
    _tw = timezone(timedelta(hours=8))
    tw_today = datetime.now(_tw).strftime("%Y-%m-%d")

    for g in today_games:
        home, away = g["home"], g["away"]
        h_era = g["home_starter"]["era"] if g.get("home_starter") else None
        a_era = g["away_starter"]["era"] if g.get("away_starter") else None
        h_inj = injury_map.get(home, [])
        a_inj = injury_map.get(away, [])

        prob = predictor.predict(home, away, h_era, a_era,
                                 home_injuries=h_inj, away_injuries=a_inj)
        pred_total = predictor.predict_total(home, away, h_era, a_era)

        entry = {
            "home": home, "away": away,
            "home_abbr": g.get("home_abbr", ""),
            "away_abbr": g.get("away_abbr", ""),
            "home_record": g.get("home_record", ""),
            "away_record": g.get("away_record", ""),
            "home_prob": round(prob * 100, 1),
            "away_prob": round((1 - prob) * 100, 1),
            "home_elo": round(predictor.elo.get(home)),
            "away_elo": round(predictor.elo.get(away)),
            "home_starter": g.get("home_starter"),
            "away_starter": g.get("away_starter"),
            "pred_total": pred_total,
            "status": g.get("status", ""),
            "home_score": g.get("home_score", 0),
            "away_score": g.get("away_score", 0),
        }

        # Injuries
        home_inj = injury_map.get(home, [])
        away_inj = injury_map.get(away, [])
        entry["injuries"] = {
            "home_out": [{"name": p["name"], "detail": p["detail"]}
                         for p in home_inj if p["status"] == "Out"],
            "away_out": [{"name": p["name"], "detail": p["detail"]}
                         for p in away_inj if p["status"] == "Out"],
            "home_gtd": [{"name": p["name"], "detail": p["detail"]}
                         for p in home_inj if p["status"] in ("Day-To-Day",)],
            "away_gtd": [{"name": p["name"], "detail": p["detail"]}
                         for p in away_inj if p["status"] in ("Day-To-Day",)],
        }

        # Taiwan date split
        tw_date = ""
        if g.get("date"):
            try:
                utc_time = datetime.fromisoformat(g["date"].replace("Z", "+00:00"))
                tw_time = utc_time.astimezone(_tw)
                tw_date = tw_time.strftime("%Y-%m-%d")
            except Exception:
                pass

        is_finished = g.get("status", "") in ("Final", "Final/OT")
        if tw_date > tw_today and not is_finished:
            output["next_games"].append(entry)
        else:
            output["games"].append(entry)

    # If all today done, fetch next day
    all_done = all(
        ge.get("status", "") in ("Final", "Postponed", "Canceled", "")
        for ge in output["games"]
    ) if output["games"] else True

    if not output["next_games"] and all_done:
        base = datetime.strptime(et_today, "%Y%m%d")
        for offset in range(1, 4):
            check = base + timedelta(days=offset)
            check_str = check.strftime("%Y%m%d")
            fetched = fetch_scoreboard(check_str)
            if fetched:
                output["next_games_date"] = check.strftime("%Y-%m-%d")
                for g in fetched:
                    home, away = g["home"], g["away"]
                    h_era = g["home_starter"]["era"] if g.get("home_starter") else None
                    a_era = g["away_starter"]["era"] if g.get("away_starter") else None
                    h_inj = injury_map.get(home, [])
                    a_inj = injury_map.get(away, [])
                    prob = predictor.predict(home, away, h_era, a_era,
                                             home_injuries=h_inj, away_injuries=a_inj)
                    pred_total = predictor.predict_total(home, away, h_era, a_era)
                    entry = {
                        "home": home, "away": away,
                        "home_abbr": g.get("home_abbr", ""),
                        "away_abbr": g.get("away_abbr", ""),
                        "home_record": g.get("home_record", ""),
                        "away_record": g.get("away_record", ""),
                        "home_prob": round(prob * 100, 1),
                        "away_prob": round((1 - prob) * 100, 1),
                        "home_elo": round(predictor.elo.get(home)),
                        "away_elo": round(predictor.elo.get(away)),
                        "home_starter": g.get("home_starter"),
                        "away_starter": g.get("away_starter"),
                        "pred_total": pred_total,
                        "status": "Scheduled",
                        "injuries": {
                            "home_out": [{"name": p["name"], "detail": p["detail"]}
                                         for p in injury_map.get(home, []) if p["status"] == "Out"],
                            "away_out": [{"name": p["name"], "detail": p["detail"]}
                                         for p in injury_map.get(away, []) if p["status"] == "Out"],
                            "home_gtd": [{"name": p["name"], "detail": p["detail"]}
                                         for p in injury_map.get(home, []) if p["status"] == "Day-To-Day"],
                            "away_gtd": [{"name": p["name"], "detail": p["detail"]}
                                         for p in injury_map.get(away, []) if p["status"] == "Day-To-Day"],
                        },
                    }
                    output["next_games"].append(entry)
                break

    if not output["next_games_date"] and output["next_games"]:
        tw_tomorrow = (datetime.now(_tw) + timedelta(days=1)).strftime("%Y-%m-%d")
        output["next_games_date"] = tw_tomorrow

    # Top standings
    sorted_teams = sorted(standings.items(), key=lambda x: x[1].get("pct", 0), reverse=True)
    for name, s in sorted_teams[:15]:
        output["standings_top"].append({
            "team": name, "wins": s["wins"], "losses": s["losses"],
            "pct": s["pct"], "diff": s["diff"],
            "rs_pg": s["rs_pg"], "ra_pg": s["ra_pg"],
        })

    # Backtest
    try:
        _real2 = sys.stdout
        sys.stdout = io.StringIO()
        bt = cmd_backtest(predictor, days)
        sys.stdout = _real2
        sys.stdout.reconfigure(encoding="utf-8")
        output["backtest"] = bt
    except Exception:
        sys.stdout = sys.__stdout__
        sys.stdout.reconfigure(encoding="utf-8")

    print(json.dumps(output, ensure_ascii=False))


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="MLB Predictor")
    parser.add_argument("--json", action="store_true", help="JSON output for dashboard")
    parser.add_argument("--backtest", action="store_true", help="Backtest predictions")
    parser.add_argument("--days", type=int, default=30, help="Days of history (default: 30)")
    args = parser.parse_args()

    predictor = MLBPredictor()

    if args.json:
        # cmd_json handles its own build_elo silently
        cmd_json(predictor, args.days)
        return

    build_elo(predictor, args.days)
    predictor.standings = fetch_standings()

    if args.backtest:
        cmd_backtest(predictor, args.days)
    else:
        # Print today's predictions
        print("\n  MLB Predictions — Today")
        print("  " + "=" * 60)
        games = fetch_scoreboard()
        for g in games:
            home, away = g["home"], g["away"]
            h_era = g["home_starter"]["era"] if g.get("home_starter") else None
            a_era = g["away_starter"]["era"] if g.get("away_starter") else None
            prob = predictor.predict(home, away, h_era, a_era)
            total = predictor.predict_total(home, away, h_era, a_era)
            pick = home if prob > 0.5 else away
            conf = max(prob, 1 - prob)

            h_sp = g["home_starter"]["name"] if g.get("home_starter") else "TBD"
            a_sp = g["away_starter"]["name"] if g.get("away_starter") else "TBD"

            print(f"\n  {away[:18]:<18} @ {home:<18} [{g.get('status','')}]")
            print(f"    Starters: {a_sp} vs {h_sp}")
            if h_era and a_era:
                print(f"    ERA: {a_era:.2f} vs {h_era:.2f}")
            print(f"    Pick: {pick} ({conf*100:.0f}%) | Total: {total}")


if __name__ == "__main__":
    main()
