"""Evolution Agent — Self-improving trading agent with SQLite persistence."""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

BOT_DIR = Path(__file__).parent
STATE_DIR = BOT_DIR / "state"
DB_PATH = STATE_DIR / "autobots.db"
TOML_PATH = BOT_DIR / "bots.toml"
TOML_BACKUP_DIR = BOT_DIR / "toml_backups"

# ── Imports from signal_manager ───────────────────────────────────────────

try:
    from signal_manager import BotAPIClient, load_toml, fetch_closes
except ImportError:
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            tomllib = None

    import re as _re

    def load_toml(path: str) -> dict:
        if tomllib:
            with open(path, "rb") as f:
                return tomllib.load(f)
        data: dict = {}
        current_section: dict = data
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = _re.match(r"\[(.+)\]", line)
                if m:
                    keys = m.group(1).split(".")
                    current_section = data
                    for k in keys:
                        if k not in current_section:
                            current_section[k] = {}
                        current_section = current_section[k]
                    continue
                m = _re.match(r'(\w+)\s*=\s*"(.+)"', line)
                if m:
                    current_section[m.group(1)] = m.group(2)
                    continue
                m = _re.match(r"(\w+)\s*=\s*(\d+\.\d+)", line)
                if m:
                    current_section[m.group(1)] = float(m.group(2))
                    continue
                m = _re.match(r"(\w+)\s*=\s*(\d+)", line)
                if m:
                    current_section[m.group(1)] = int(m.group(2))
                    continue
                m = _re.match(r"(\w+)\s*=\s*(true|false)", line)
                if m:
                    current_section[m.group(1)] = m.group(2) == "true"
        return data

    BotAPIClient = None  # type: ignore[assignment,misc]
    fetch_closes = None  # type: ignore[assignment]


# ── Logging ───────────────────────────────────────────────────────────────

def setup_logger(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("evolution")
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

        fh = logging.FileHandler(BOT_DIR / "evolution_agent.log", encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


log = setup_logger()


# ══════════════════════════════════════════════════════════════════════════
#  SQLite Database
# ══════════════════════════════════════════════════════════════════════════

class EvolutionDB:
    """SQLite persistence for the evolution agent."""

    def __init__(self, db_path: str | Path):
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        with self.conn:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS bot_health (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    bot_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    health_score INTEGER NOT NULL,
                    real_roi REAL,
                    backtest_roi REAL,
                    divergence REAL,
                    last_price REAL,
                    grid_bottom REAL,
                    grid_top REAL,
                    grid_utilization REAL,
                    recommendation TEXT
                );

                CREATE TABLE IF NOT EXISTS journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    bot_name TEXT,
                    symbol TEXT,
                    action TEXT NOT NULL,
                    reasoning TEXT NOT NULL,
                    context_json TEXT,
                    params_before TEXT,
                    params_after TEXT,
                    expected_improvement REAL,
                    outcome_roi REAL,
                    outcome_measured_at TEXT
                );

                CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    total_value REAL,
                    total_capital REAL,
                    drawdown REAL,
                    risk_mode TEXT,
                    allocations_json TEXT
                );

                CREATE TABLE IF NOT EXISTS param_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    bot_name TEXT NOT NULL,
                    param_name TEXT NOT NULL,
                    old_value TEXT,
                    new_value TEXT,
                    reason TEXT,
                    backtest_improvement REAL,
                    cooldown_until TEXT
                );

                CREATE TABLE IF NOT EXISTS correlations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    bot_a TEXT NOT NULL,
                    bot_b TEXT NOT NULL,
                    correlation REAL NOT NULL,
                    prev_correlation REAL,
                    change REAL
                );

                CREATE TABLE IF NOT EXISTS knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    pattern_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    confidence REAL,
                    evidence_count INTEGER DEFAULT 1,
                    details_json TEXT
                );
            """)

    # ── Record methods ────────────────────────────────────────────────────

    def record_health(self, bot_name: str, symbol: str, health_score: int,
                      real_roi: float, backtest_roi: float | None,
                      divergence: float | None, last_price: float,
                      grid_bottom: float, grid_top: float,
                      grid_utilization: float, recommendation: str):
        now = datetime.now(timezone.utc).isoformat()
        with self.conn:
            self.conn.execute(
                """INSERT INTO bot_health
                   (timestamp, bot_name, symbol, health_score, real_roi, backtest_roi,
                    divergence, last_price, grid_bottom, grid_top, grid_utilization, recommendation)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (now, bot_name, symbol, health_score, real_roi, backtest_roi,
                 divergence, last_price, grid_bottom, grid_top, grid_utilization, recommendation),
            )

    def record_journal(self, run_id: str, event_type: str, bot_name: str | None,
                       action: str, reasoning: str, symbol: str | None = None,
                       context_json: str | None = None,
                       params_before: str | None = None,
                       params_after: str | None = None,
                       expected_improvement: float | None = None):
        now = datetime.now(timezone.utc).isoformat()
        with self.conn:
            self.conn.execute(
                """INSERT INTO journal
                   (timestamp, run_id, event_type, bot_name, symbol, action, reasoning,
                    context_json, params_before, params_after, expected_improvement)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (now, run_id, event_type, bot_name, symbol, action, reasoning,
                 context_json, params_before, params_after, expected_improvement),
            )

    def record_snapshot(self, total_value: float, total_capital: float,
                        drawdown: float, risk_mode: str, allocations: dict):
        now = datetime.now(timezone.utc).isoformat()
        with self.conn:
            self.conn.execute(
                """INSERT INTO portfolio_snapshots
                   (timestamp, total_value, total_capital, drawdown, risk_mode, allocations_json)
                   VALUES (?,?,?,?,?,?)""",
                (now, total_value, total_capital, drawdown, risk_mode,
                 json.dumps(allocations)),
            )

    def record_param_change(self, bot_name: str, param_name: str,
                            old_value: str, new_value: str, reason: str,
                            backtest_improvement: float | None = None,
                            cooldown_days: int = 7):
        now = datetime.now(timezone.utc)
        cooldown_until = (now + timedelta(days=cooldown_days)).isoformat()
        with self.conn:
            self.conn.execute(
                """INSERT INTO param_changes
                   (timestamp, bot_name, param_name, old_value, new_value,
                    reason, backtest_improvement, cooldown_until)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (now.isoformat(), bot_name, param_name, old_value, new_value,
                 reason, backtest_improvement, cooldown_until),
            )

    def record_correlation(self, bot_a: str, bot_b: str,
                           correlation: float, prev_correlation: float | None):
        now = datetime.now(timezone.utc).isoformat()
        change = (correlation - prev_correlation) if prev_correlation is not None else None
        with self.conn:
            self.conn.execute(
                """INSERT INTO correlations
                   (timestamp, bot_a, bot_b, correlation, prev_correlation, change)
                   VALUES (?,?,?,?,?,?)""",
                (now, bot_a, bot_b, correlation, prev_correlation, change),
            )

    def add_knowledge(self, pattern_type: str, description: str,
                      confidence: float, details: dict | None = None):
        now = datetime.now(timezone.utc).isoformat()
        with self.conn:
            self.conn.execute(
                """INSERT INTO knowledge
                   (created_at, updated_at, pattern_type, description, confidence, details_json)
                   VALUES (?,?,?,?,?,?)""",
                (now, now, pattern_type, description, confidence,
                 json.dumps(details) if details else None),
            )

    # ── Query methods ─────────────────────────────────────────────────────

    def get_recent_health(self, bot_name: str, days: int = 30) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.conn.execute(
            """SELECT * FROM bot_health
               WHERE bot_name = ? AND timestamp >= ?
               ORDER BY timestamp DESC""",
            (bot_name, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_pending_outcomes(self) -> list[dict]:
        rows = self.conn.execute(
            """SELECT * FROM journal
               WHERE outcome_roi IS NULL
                 AND event_type IN ('param_change', 'bot_add', 'grid_adjust', 'reoptimize')
               ORDER BY timestamp ASC""",
        ).fetchall()
        return [dict(r) for r in rows]

    def update_outcome(self, journal_id: int, roi: float):
        now = datetime.now(timezone.utc).isoformat()
        with self.conn:
            self.conn.execute(
                """UPDATE journal SET outcome_roi = ?, outcome_measured_at = ?
                   WHERE id = ?""",
                (roi, now, journal_id),
            )

    def get_knowledge(self, pattern_type: str | None = None) -> list[dict]:
        if pattern_type:
            rows = self.conn.execute(
                "SELECT * FROM knowledge WHERE pattern_type = ? ORDER BY confidence DESC",
                (pattern_type,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM knowledge ORDER BY pattern_type, confidence DESC",
            ).fetchall()
        return [dict(r) for r in rows]

    def is_on_cooldown(self, bot_name: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        row = self.conn.execute(
            """SELECT COUNT(*) AS cnt FROM param_changes
               WHERE bot_name = ? AND cooldown_until > ?""",
            (bot_name, now),
        ).fetchone()
        return row["cnt"] > 0

    def get_bot_health_trend(self, bot_name: str, days: int = 14) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.conn.execute(
            """SELECT timestamp, health_score, real_roi
               FROM bot_health
               WHERE bot_name = ? AND timestamp >= ?
               ORDER BY timestamp ASC""",
            (bot_name, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_last_correlation(self, bot_a: str, bot_b: str) -> float | None:
        row = self.conn.execute(
            """SELECT correlation FROM correlations
               WHERE (bot_a = ? AND bot_b = ?) OR (bot_a = ? AND bot_b = ?)
               ORDER BY timestamp DESC LIMIT 1""",
            (bot_a, bot_b, bot_b, bot_a),
        ).fetchone()
        return row["correlation"] if row else None

    def get_latest_healths(self) -> list[dict]:
        """Latest health record per bot."""
        rows = self.conn.execute(
            """SELECT bh.* FROM bot_health bh
               INNER JOIN (
                   SELECT bot_name, MAX(timestamp) AS max_ts
                   FROM bot_health GROUP BY bot_name
               ) latest ON bh.bot_name = latest.bot_name
                       AND bh.timestamp = latest.max_ts
               ORDER BY bh.health_score ASC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_journal(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM journal ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_latest_correlations(self) -> list[dict]:
        rows = self.conn.execute(
            """SELECT c.* FROM correlations c
               INNER JOIN (
                   SELECT bot_a, bot_b, MAX(timestamp) AS max_ts
                   FROM correlations GROUP BY bot_a, bot_b
               ) latest ON c.bot_a = latest.bot_a
                       AND c.bot_b = latest.bot_b
                       AND c.timestamp = latest.max_ts
               ORDER BY c.correlation DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def get_portfolio_history(self, days: int = 30) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.conn.execute(
            """SELECT timestamp, total_value, drawdown, risk_mode
               FROM portfolio_snapshots
               WHERE timestamp >= ?
               ORDER BY timestamp ASC""",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self.conn.close()


# ══════════════════════════════════════════════════════════════════════════
#  Health Scoring
# ══════════════════════════════════════════════════════════════════════════

def compute_health_score(bot_name: str, real_roi: float,
                         backtest_roi: float | None, last_price: float,
                         grid_bottom: float, grid_top: float,
                         drawdown: float) -> int:
    """Compute a 0-100 health score for a bot."""
    score = 0

    # ROI alignment (30 pts)
    if backtest_roi and backtest_roi > 0:
        alignment = min(1.0, max(0, real_roi / backtest_roi))
        score += int(alignment * 30)
    else:
        score += 15  # unknown backtest, neutral

    # Absolute ROI (25 pts)
    if real_roi >= 0.10:
        score += 25
    elif real_roi >= 0.05:
        score += 20
    elif real_roi >= 0:
        score += 15
    elif real_roi >= -0.05:
        score += 10
    elif real_roi >= -0.10:
        score += 5

    # Grid utilization (15 pts)
    if grid_bottom < last_price < grid_top:
        center = (grid_top + grid_bottom) / 2
        half = (grid_top - grid_bottom) / 2
        dist = abs(last_price - center) / half if half > 0 else 1
        score += int((1 - min(1, dist)) * 15)

    # Drawdown headroom (15 pts)
    headroom = max(0, 0.15 - abs(drawdown)) / 0.15
    score += int(headroom * 15)

    # Base stability (15 pts) - given by default
    score += 15

    return min(100, max(0, score))


def classify_recommendation(health_score: int, real_roi: float) -> str:
    """Map health score to a recommendation."""
    if health_score >= 70:
        return "hold"
    if health_score >= 50:
        if real_roi < -0.05:
            return "reoptimize"
        return "hold"
    if health_score >= 30:
        return "reoptimize"
    if real_roi < -0.20:
        return "drop"
    return "reduce"


# ══════════════════════════════════════════════════════════════════════════
#  Evolution Agent
# ══════════════════════════════════════════════════════════════════════════

class EvolutionAgent:
    """Self-improving trading agent with SQLite-backed learning."""

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.run_id = ""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        TOML_BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        self.db = EvolutionDB(DB_PATH)
        self._reload_bots()

        # API client
        self.client = None
        if BotAPIClient is not None:
            try:
                self.client = BotAPIClient()
            except Exception as e:
                log.warning("Failed to init BotAPIClient: %s", e)

    def _reload_bots(self):
        """Load bot configs from bots.toml."""
        cfg = load_toml(str(TOML_PATH))
        self.bots: dict[str, dict] = cfg.get("bots", {})

    def _load_bot_state(self, bot_name: str) -> dict:
        """Load a bot's state JSON file."""
        state_path = STATE_DIR / f"{bot_name}.json"
        if state_path.exists():
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _backup_toml(self):
        """Copy bots.toml to toml_backups/ with timestamp."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dest = TOML_BACKUP_DIR / f"bots_{ts}.toml"
        shutil.copy2(TOML_PATH, dest)
        log.info("Backed up bots.toml -> %s", dest.name)

    # ── Evaluate all bots ─────────────────────────────────────────────────

    def evaluate_all_bots(self) -> dict[str, dict]:
        """Build health report for every bot."""
        from portfolio_agent import collect_bot_metrics

        health_reports: dict[str, dict] = {}

        # Collect live metrics if client is available
        metrics = {}
        if self.client:
            try:
                metrics = collect_bot_metrics(self.client, self.bots)
            except Exception as e:
                log.warning("collect_bot_metrics failed: %s", e)

        for bot_name, cfg in self.bots.items():
            symbol = cfg.get("symbol", "")
            state = self._load_bot_state(bot_name)
            last_price = state.get("last_price", 0.0)
            grid_bottom = float(cfg.get("grid_bottom", 0))
            grid_top = float(cfg.get("grid_top", 0))

            # Get ROI from metrics or state
            m = metrics.get(bot_name, {})
            real_roi = m.get("roi", 0.0)

            # Grid utilization
            grid_util = 0.0
            if grid_bottom < last_price < grid_top:
                center = (grid_top + grid_bottom) / 2
                half = (grid_top - grid_bottom) / 2
                grid_util = 1.0 - (abs(last_price - center) / half if half > 0 else 1)

            # Backtest ROI (try loading from optim output)
            backtest_roi = self._get_backtest_roi(symbol)
            divergence = None
            if backtest_roi is not None and backtest_roi != 0:
                divergence = real_roi - backtest_roi

            # Drawdown estimate from ROI
            drawdown = min(0, real_roi)

            score = compute_health_score(
                bot_name, real_roi, backtest_roi, last_price,
                grid_bottom, grid_top, drawdown,
            )
            recommendation = classify_recommendation(score, real_roi)

            health_reports[bot_name] = {
                "bot_name": bot_name,
                "symbol": symbol,
                "health_score": score,
                "real_roi": real_roi,
                "backtest_roi": backtest_roi,
                "divergence": divergence,
                "last_price": last_price,
                "grid_bottom": grid_bottom,
                "grid_top": grid_top,
                "grid_utilization": grid_util,
                "recommendation": recommendation,
            }

        return health_reports

    def _get_backtest_roi(self, symbol: str) -> float | None:
        """Try reading backtest ROI from optimization output."""
        optim_path = BOT_DIR / "output" / f"optim_{symbol}.json"
        if not optim_path.exists():
            return None
        try:
            with open(optim_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            baseline = data.get("v6_baseline")
            if baseline:
                return baseline.get("return_pct", 0) / 100.0
            top = data.get("top_by_return", [])
            if top:
                return top[0].get("return_pct", 0) / 100.0
        except Exception:
            pass
        return None

    # ── Auto-correction ───────────────────────────────────────────────────

    def auto_correct(self, health_reports: dict[str, dict]) -> list[dict]:
        """Auto-correct underperforming bots."""
        actions: list[dict] = []
        for bot_name, health in health_reports.items():
            if self.db.is_on_cooldown(bot_name):
                log.info("[%s] On cooldown — skipping auto-correct", bot_name.upper())
                continue

            score = health["health_score"]
            roi = health["real_roi"]

            if score < 40 and roi < -0.10:
                log.info("[%s] Low health (%d) + negative ROI (%.1f%%) — re-optimizing",
                         bot_name.upper(), score, roi * 100)
                result = self._run_reoptimization(health["symbol"])
                if result and result.get("top_by_return"):
                    best = result["top_by_return"][0]
                    improvement = best.get("return_pct", 0) - (roi * 100)
                    if improvement > 5.0:
                        self._apply_param_change(bot_name, best,
                                                 reason=f"auto-correct: health={score}, roi={roi:.1%}")
                        actions.append({
                            "bot": bot_name, "action": "reoptimize",
                            "improvement": improvement,
                        })
                        self.db.record_journal(
                            self.run_id, "param_change", bot_name,
                            "reoptimize",
                            f"Auto-reoptimize: health={score}, ROI={roi:.1%}, improvement={improvement:.1f}%",
                            symbol=health["symbol"],
                            expected_improvement=improvement,
                        )
            elif score < 20 and roi < -0.20:
                actions.append({"bot": bot_name, "action": "drop_candidate"})
                self.db.record_journal(
                    self.run_id, "evaluation", bot_name,
                    "drop_candidate",
                    f"Drop candidate: health={score}, ROI={roi:.1%}",
                    symbol=health["symbol"],
                )

        return actions

    def _run_reoptimization(self, symbol: str) -> dict | None:
        """Run optimize_three_kingdoms.py for a symbol via subprocess."""
        log.info("Running re-optimization for %s ...", symbol)
        env = {**os.environ, "BT_SYMBOL": symbol, "BT_INTERVAL": "60M"}
        try:
            result = subprocess.run(
                [sys.executable, str(BOT_DIR / "optimize_three_kingdoms.py")],
                env=env, capture_output=True, timeout=300, text=True,
            )
            if result.returncode != 0:
                log.warning("Optimization failed for %s: %s", symbol, result.stderr[:500])
                return None
        except subprocess.TimeoutExpired:
            log.warning("Optimization timed out for %s", symbol)
            return None
        except Exception as e:
            log.warning("Optimization error for %s: %s", symbol, e)
            return None

        optim_path = BOT_DIR / "output" / f"optim_{symbol}.json"
        if optim_path.exists():
            with open(optim_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def _apply_param_change(self, bot_name: str, new_params: dict,
                            reason: str = "optimization"):
        """Record param changes to DB. Does NOT modify bots.toml in dry-run."""
        cfg = self.bots.get(bot_name, {})
        param_map = {
            "liu_bei": "liu_bei", "guan_yu": "guan_yu", "zhang_fei": "zhang_fei",
            "dist_pct": "dist_pct", "no_bounce": "disable_bounce",
            "grid_bottom": "grid_bottom", "grid_top": "grid_top",
        }
        for src_key, toml_key in param_map.items():
            if src_key in new_params:
                old_val = str(cfg.get(toml_key, ""))
                new_val = str(new_params[src_key])
                if old_val != new_val:
                    self.db.record_param_change(
                        bot_name, toml_key, old_val, new_val,
                        reason=reason,
                        backtest_improvement=new_params.get("return_pct"),
                    )
                    log.info("[%s] Param %s: %s -> %s", bot_name.upper(),
                             toml_key, old_val, new_val)

        if not self.dry_run:
            self._backup_toml()
            log.info("[%s] LIVE mode: bots.toml update not yet implemented — params recorded to DB",
                     bot_name.upper())

    # ── Grid range auto-adjust ────────────────────────────────────────────

    def check_grid_ranges(self):
        """Check if price is near grid boundaries, propose new range."""
        for bot_name, cfg in self.bots.items():
            state = self._load_bot_state(bot_name)
            price = state.get("last_price", 0)
            bottom = float(cfg.get("grid_bottom", 0))
            top = float(cfg.get("grid_top", 0))
            if price <= 0 or bottom <= 0 or top <= bottom:
                continue

            margin = (top - bottom) * 0.05  # 5% margin
            if price < bottom + margin or price > top - margin:
                new_bottom = round(price * 0.85, 2)
                new_top = round(price * 1.15, 2)
                log.warning("[%s] Price %.2f near grid edge [%.2f - %.2f] -> propose [%.2f - %.2f]",
                            bot_name.upper(), price, bottom, top, new_bottom, new_top)
                self._apply_param_change(bot_name, {
                    "grid_bottom": str(new_bottom),
                    "grid_top": str(new_top),
                }, reason=f"grid_adjust: price {price:.2f} near edge")
                self.db.record_journal(
                    self.run_id, "grid_adjust", bot_name,
                    f"grid_adjust_{bot_name}",
                    f"Price {price:.2f} near grid edge [{bottom}-{top}], proposed [{new_bottom}-{new_top}]",
                    symbol=cfg.get("symbol"),
                )

    # ── Correlation detection ─────────────────────────────────────────────

    def compute_correlations(self):
        """Fetch 30-day closes for all bots, compute pairwise Pearson correlation."""
        if not self.client or fetch_closes is None:
            log.info("Skipping correlations — no API client")
            return

        closes_map: dict[str, list[float]] = {}
        for bot_name, cfg in self.bots.items():
            symbol = cfg.get("symbol", "")
            if not symbol:
                continue
            try:
                closes = fetch_closes(self.client, symbol, "60M", 720)  # 30 days * 24h
                if closes and len(closes) > 48:
                    closes_map[bot_name] = closes
            except Exception as e:
                log.warning("[%s] Failed to fetch closes for correlation: %s",
                            bot_name.upper(), e)

        bot_names = list(closes_map.keys())
        for i in range(len(bot_names)):
            for j in range(i + 1, len(bot_names)):
                a, b = bot_names[i], bot_names[j]
                min_len = min(len(closes_map[a]), len(closes_map[b]))
                if min_len < 48:
                    continue
                corr = float(np.corrcoef(
                    closes_map[a][-min_len:],
                    closes_map[b][-min_len:],
                )[0, 1])
                prev = self.db.get_last_correlation(a, b)
                self.db.record_correlation(a, b, corr, prev)

                if prev is not None and abs(corr - prev) > 0.3:
                    self.db.record_journal(
                        self.run_id, "correlation_alert", a,
                        f"correlation_shift_{a}_{b}",
                        f"Correlation between {a} and {b} changed from {prev:.2f} to {corr:.2f}",
                        symbol=self.bots[a].get("symbol"),
                    )
                    log.warning("Correlation shift: %s <-> %s: %.2f -> %.2f",
                                a, b, prev, corr)

    # ── Measure past decisions ────────────────────────────────────────────

    def measure_outcomes(self):
        """Check pending journal entries and fill in outcome ROI if enough time passed."""
        pending = self.db.get_pending_outcomes()
        for entry in pending:
            entry_time = datetime.fromisoformat(entry["timestamp"])
            age_days = (datetime.now(timezone.utc) - entry_time).days
            if age_days < 7:
                continue  # too soon to measure

            bot_name = entry.get("bot_name")
            if not bot_name:
                continue

            # Get current health
            recent = self.db.get_recent_health(bot_name, days=3)
            if recent:
                current_roi = recent[0].get("real_roi", 0)
                self.db.update_outcome(entry["id"], current_roi)
                log.info("[%s] Outcome measured for journal #%d: ROI=%.2f%%",
                         bot_name.upper(), entry["id"], current_roi * 100)

    # ── Risk adaptation ───────────────────────────────────────────────────

    def adapt_risk(self, portfolio: dict):
        """Adjust risk mode based on portfolio drawdown."""
        drawdown = portfolio.get("drawdown", 0)
        current_mode = portfolio.get("risk_mode", "normal")

        if drawdown < -0.15:
            new_mode = "conservative"
        elif drawdown < -0.05:
            new_mode = "normal"
        else:
            new_mode = "aggressive" if drawdown > 0.05 else "normal"

        if new_mode != current_mode:
            self.db.record_journal(
                self.run_id, "risk_shift", None,
                f"risk_{current_mode}_to_{new_mode}",
                f"Portfolio drawdown {drawdown:.1%} triggered risk mode change",
            )
            log.info("Risk mode: %s -> %s (drawdown: %.1f%%)",
                     current_mode, new_mode, drawdown * 100)

    # ── Weekly review ─────────────────────────────────────────────────────

    def weekly_review(self):
        """Rank bots, drop worst, scan for new candidates."""
        log.info("=" * 40)
        log.info("Weekly Review")
        log.info("=" * 40)

        healths: dict[str, dict] = {}
        for bot_name in self.bots:
            recent = self.db.get_recent_health(bot_name, days=7)
            if recent:
                avg_score = sum(r["health_score"] for r in recent) / len(recent)
                avg_roi = sum(r["real_roi"] for r in recent) / len(recent)
                healths[bot_name] = {"avg_score": avg_score, "avg_roi": avg_roi}

        if not healths:
            log.info("No health data yet — skipping weekly review")
            return

        # Drop worst if conditions met
        if len(self.bots) > 5 and healths:
            worst = min(healths.items(), key=lambda x: x[1]["avg_score"])
            if worst[1]["avg_roi"] < -0.20:
                log.warning("[%s] DROP CANDIDATE: avg ROI %.1f%%, avg health %.0f",
                            worst[0].upper(), worst[1]["avg_roi"] * 100,
                            worst[1]["avg_score"])
                if not self.dry_run:
                    self._comment_out_bot(worst[0])
                self.db.record_journal(
                    self.run_id, "bot_drop", worst[0], "drop",
                    f'Dropped: avg ROI {worst[1]["avg_roi"]:.1%}, '
                    f'avg health {worst[1]["avg_score"]:.0f}',
                    symbol=self.bots.get(worst[0], {}).get("symbol"),
                )

        # Scan new candidates
        if len(self.bots) < 12:
            candidates = [
                "SPYX_USDT_PERP", "NVDAX_USDT_PERP", "TSLAX_USDT_PERP",
                "AAPLX_USDT_PERP", "METAX_USDT_PERP",
            ]
            active_symbols = {cfg.get("symbol", "") for cfg in self.bots.values()}
            for sym in candidates:
                if sym in active_symbols:
                    continue
                result = self._run_reoptimization(sym)
                if result and result.get("top_by_return"):
                    best = result["top_by_return"][0]
                    if best.get("return_pct", 0) > 20 and best.get("profit_factor", 0) > 1.3:
                        log.info("New candidate: %s (return=%.1f%%, PF=%.2f)",
                                 sym, best["return_pct"], best["profit_factor"])
                        self.db.record_journal(
                            self.run_id, "bot_add", None, f"candidate_{sym}",
                            f'Candidate: return={best["return_pct"]:.1f}%, '
                            f'PF={best["profit_factor"]:.2f}',
                            symbol=sym,
                        )

    def _comment_out_bot(self, bot_name: str):
        """Comment out a bot section in bots.toml."""
        self._backup_toml()
        with open(TOML_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()

        new_lines = []
        in_section = False
        for line in lines:
            if line.strip().startswith(f"[bots.{bot_name}]"):
                in_section = True
                new_lines.append(f"# DROPPED by evolution_agent\n")
                new_lines.append(f"# {line}")
                continue
            if in_section:
                if line.strip().startswith("["):
                    in_section = False
                    new_lines.append(line)
                else:
                    new_lines.append(f"# {line}")
                continue
            new_lines.append(line)

        with open(TOML_PATH, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        log.info("[%s] Commented out in bots.toml", bot_name.upper())

    def _is_weekly_review_due(self) -> bool:
        """Check if 7 days since last weekly review journal entry."""
        rows = self.db.conn.execute(
            """SELECT timestamp FROM journal
               WHERE event_type = 'bot_drop' OR action LIKE 'candidate_%'
               ORDER BY timestamp DESC LIMIT 1"""
        ).fetchone()
        if not rows:
            return True
        last = datetime.fromisoformat(rows["timestamp"])
        return (datetime.now(timezone.utc) - last).days >= 7

    # ── Portfolio loading ─────────────────────────────────────────────────

    def _load_portfolio(self) -> dict:
        """Load portfolio state from portfolio.json."""
        portfolio_path = STATE_DIR / "portfolio.json"
        if portfolio_path.exists():
            try:
                with open(portfolio_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        # Default portfolio
        total_inv = sum(float(c.get("investment", 0)) for c in self.bots.values())
        return {
            "total_value": total_inv,
            "total_capital": total_inv,
            "drawdown": 0.0,
            "risk_mode": "normal",
            "allocations": {n: float(c.get("investment", 0)) for n, c in self.bots.items()},
        }

    # ── Force re-optimize ─────────────────────────────────────────────────

    def force_reoptimize(self, bot_name: str):
        """Force re-optimization for a specific bot."""
        self.run_id = f"evo_force_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        cfg = self.bots.get(bot_name)
        if not cfg:
            log.error("Bot '%s' not found in bots.toml", bot_name)
            return
        symbol = cfg.get("symbol", "")
        log.info("Force re-optimization for %s (%s)", bot_name, symbol)
        result = self._run_reoptimization(symbol)
        if result:
            log.info("Optimization result for %s:", symbol)
            top = result.get("top_by_return", [])
            for i, r in enumerate(top[:3]):
                log.info("  #%d: return=%.2f%% PF=%.2f DD=%.2f%% LB=%s GY=%s ZF=%s dist=%.1f%%",
                         i + 1, r.get("return_pct", 0), r.get("profit_factor", 0),
                         r.get("max_dd", 0), r.get("liu_bei"), r.get("guan_yu"),
                         r.get("zhang_fei"), r.get("dist_pct", 0))
        else:
            log.warning("No optimization result for %s", symbol)

    # ── Summary ───────────────────────────────────────────────────────────

    def print_summary(self, health_reports: dict[str, dict]):
        """Print a clear summary table."""
        log.info("")
        log.info("=" * 80)
        log.info("  %-10s  %6s  %8s  %8s  %10s  %7s  %s",
                 "BOT", "HEALTH", "ROI", "BT ROI", "PRICE", "GRID%", "ACTION")
        log.info("-" * 80)

        for name in sorted(health_reports, key=lambda n: health_reports[n]["health_score"]):
            h = health_reports[name]
            bt_str = f"{h['backtest_roi']:.1%}" if h["backtest_roi"] is not None else "n/a"
            grid_str = f"{h['grid_utilization']:.0%}" if h["grid_utilization"] else "OOB"
            log.info("  %-10s  %6d  %+7.1f%%  %8s  %10.2f  %7s  %s",
                     name.upper(), h["health_score"], h["real_roi"] * 100,
                     bt_str, h["last_price"], grid_str, h["recommendation"])

        log.info("=" * 80)
        log.info("Run ID: %s  |  Dry-run: %s  |  Bots: %d",
                 self.run_id, self.dry_run, len(self.bots))
        log.info("")

    # ── Main orchestration ────────────────────────────────────────────────

    def run(self):
        self.run_id = f"evo_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        log.info("=" * 60)
        log.info("Evolution Agent — Daily Review")
        log.info("Run ID: %s  |  Dry-run: %s", self.run_id, self.dry_run)
        log.info("=" * 60)

        # 1. Evaluate all bots
        health_reports = self.evaluate_all_bots()

        # 2. Record health to DB
        for name, h in health_reports.items():
            self.db.record_health(
                bot_name=h["bot_name"], symbol=h["symbol"],
                health_score=h["health_score"], real_roi=h["real_roi"],
                backtest_roi=h["backtest_roi"], divergence=h["divergence"],
                last_price=h["last_price"], grid_bottom=h["grid_bottom"],
                grid_top=h["grid_top"], grid_utilization=h["grid_utilization"],
                recommendation=h["recommendation"],
            )

        # 3. Record portfolio snapshot
        portfolio = self._load_portfolio()
        self.db.record_snapshot(
            total_value=portfolio.get("total_value", 0),
            total_capital=portfolio.get("total_capital", 0),
            drawdown=portfolio.get("drawdown", 0),
            risk_mode=portfolio.get("risk_mode", "normal"),
            allocations=portfolio.get("allocations", {}),
        )

        # 4. Risk adaptation
        self.adapt_risk(portfolio)

        # 5. Auto-correct underperformers
        if not self.dry_run:
            corrections = self.auto_correct(health_reports)
            if corrections:
                log.info("Auto-corrections: %s", json.dumps(corrections, indent=2))
        else:
            # In dry-run, still evaluate but don't apply
            corrections = self.auto_correct(health_reports)
            if corrections:
                log.info("[DRY-RUN] Would auto-correct: %s",
                         json.dumps(corrections, indent=2))

        # 6. Check grid ranges
        self.check_grid_ranges()

        # 7. Measure past decision outcomes
        self.measure_outcomes()

        # 8. Weekly review (every 7 days)
        if self._is_weekly_review_due():
            self.weekly_review()
            # 9. Correlation check (weekly)
            self.compute_correlations()

        # 10. Print summary
        self.print_summary(health_reports)


# ══════════════════════════════════════════════════════════════════════════
#  CLI Query Mode
# ══════════════════════════════════════════════════════════════════════════

def run_query(query_type: str):
    """Print useful reports from SQLite."""
    db = EvolutionDB(DB_PATH)

    if query_type == "health":
        rows = db.get_latest_healths()
        if not rows:
            print("No health data yet.")
            return
        print(f"\n{'BOT':<12} {'SCORE':>6} {'ROI':>8} {'PRICE':>12} {'GRID':>10} {'ACTION':<12}")
        print("-" * 62)
        for r in rows:
            print(f"{r['bot_name']:<12} {r['health_score']:>6d} "
                  f"{r['real_roi']*100:>+7.1f}% {r['last_price']:>12.2f} "
                  f"{r['grid_utilization']*100:>9.0f}% {r['recommendation']:<12}")

    elif query_type == "journal":
        rows = db.get_recent_journal(20)
        if not rows:
            print("No journal entries yet.")
            return
        print(f"\n{'TIME':<22} {'TYPE':<18} {'BOT':<10} {'ACTION':<30}")
        print("-" * 82)
        for r in rows:
            ts = r["timestamp"][:19]
            bot = r["bot_name"] or "-"
            print(f"{ts:<22} {r['event_type']:<18} {bot:<10} {r['action']:<30}")

    elif query_type == "correlations":
        rows = db.get_latest_correlations()
        if not rows:
            print("No correlation data yet.")
            return
        print(f"\n{'BOT A':<12} {'BOT B':<12} {'CORR':>8} {'PREV':>8} {'CHANGE':>8}")
        print("-" * 50)
        for r in rows:
            prev = f"{r['prev_correlation']:.2f}" if r["prev_correlation"] is not None else "n/a"
            chg = f"{r['change']:+.2f}" if r["change"] is not None else "n/a"
            print(f"{r['bot_a']:<12} {r['bot_b']:<12} {r['correlation']:>8.2f} "
                  f"{prev:>8} {chg:>8}")

    elif query_type == "knowledge":
        rows = db.get_knowledge()
        if not rows:
            print("No learned patterns yet.")
            return
        print(f"\n{'TYPE':<18} {'CONFIDENCE':>10} {'COUNT':>6} DESCRIPTION")
        print("-" * 70)
        for r in rows:
            print(f"{r['pattern_type']:<18} {r['confidence']:>10.2f} "
                  f"{r['evidence_count']:>6d} {r['description'][:40]}")

    elif query_type == "history":
        rows = db.get_portfolio_history(30)
        if not rows:
            print("No portfolio history yet.")
            return
        print(f"\n{'DATE':<22} {'VALUE':>10} {'DD':>8} {'MODE':<14}")
        print("-" * 56)
        for r in rows:
            ts = r["timestamp"][:19]
            print(f"{ts:<22} {r['total_value']:>10.2f} "
                  f"{r['drawdown']*100:>+7.1f}% {r['risk_mode']:<14}")

    else:
        print(f"Unknown query type: {query_type}")
        print("Available: health, journal, correlations, knowledge, history")

    db.close()


# ══════════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evolution Agent — Self-improving trading bot manager")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Dry-run mode (default)")
    parser.add_argument("--live", action="store_true",
                        help="Enable live mode (writes changes)")
    parser.add_argument("--loop", action="store_true",
                        help="Run in a loop every 24h")
    parser.add_argument("--force-reoptimize", type=str, metavar="BOT",
                        help="Force re-optimize a specific bot")
    parser.add_argument("--query", type=str, metavar="TYPE",
                        choices=["health", "journal", "correlations", "knowledge", "history"],
                        help="Query mode: health|journal|correlations|knowledge|history")
    args = parser.parse_args()

    if args.query:
        run_query(args.query)
        sys.exit(0)

    dry_run = not args.live

    agent = EvolutionAgent(dry_run=dry_run)

    if args.force_reoptimize:
        agent.force_reoptimize(args.force_reoptimize)
    elif args.loop:
        while True:
            try:
                agent.run()
            except Exception as e:
                log.error("Run failed: %s", e, exc_info=True)
            log.info("Sleeping 24h until next review...")
            time.sleep(86400)
    else:
        agent.run()
