"""Shared utilities for Autobots trading system."""
from __future__ import annotations

import sys
import os
import json
import logging
import time
import hashlib
import hmac
import re
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

BOT_DIR = Path(__file__).parent
STATE_DIR = BOT_DIR / "state"
TOML_PATH = BOT_DIR / "bots.toml"

# ── TOML Parser ──────────────────────────────────────────────────────────────

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]


def load_toml(path: str) -> dict:
    """Parse TOML with tomllib fallback to manual parser."""
    if tomllib:
        with open(path, "rb") as f:
            return tomllib.load(f)
    # Fallback: manual parse for simple TOML
    data: dict = {}
    current_section: dict = data
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"\[(.+)\]", line)
            if m:
                keys = m.group(1).split(".")
                current_section = data
                for k in keys:
                    if k not in current_section:
                        current_section[k] = {}
                    current_section = current_section[k]
                continue
            m = re.match(r'(\w+)\s*=\s*"(.+)"', line)
            if m:
                current_section[m.group(1)] = m.group(2)
                continue
            m = re.match(r"(\w+)\s*=\s*(\d+\.\d+)", line)
            if m:
                current_section[m.group(1)] = float(m.group(2))
                continue
            m = re.match(r"(\w+)\s*=\s*(\d+)", line)
            if m:
                current_section[m.group(1)] = int(m.group(2))
                continue
            m = re.match(r"(\w+)\s*=\s*(true|false)", line)
            if m:
                current_section[m.group(1)] = m.group(2) == "true"
    return data


# ── File Locking ─────────────────────────────────────────────────────────────

@contextmanager
def file_lock(path: str | Path, timeout: int = 10):
    """Simple file-based lock using .lock files.

    Usage: with file_lock('state/xaut.json'): ...

    Stale locks (>60 seconds old) are automatically removed.
    If the lock cannot be acquired within *timeout* seconds the stale lock
    is force-removed and the caller proceeds.
    """
    lock_path = Path(str(path) + ".lock")
    start = time.time()
    while True:
        try:
            # Try to create lock file exclusively (atomic on POSIX and Windows)
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            # Check if lock is stale (>60 seconds old)
            try:
                if time.time() - os.path.getmtime(str(lock_path)) > 60:
                    os.unlink(str(lock_path))
                    continue
            except OSError:
                pass
            if time.time() - start > timeout:
                # Force remove lock and continue
                try:
                    os.unlink(str(lock_path))
                except OSError:
                    pass
                continue
            time.sleep(0.1)
    try:
        yield
    finally:
        try:
            os.unlink(str(lock_path))
        except OSError:
            pass


# ── State File I/O with Locking ───────────────────────────────────────────────

def load_state(bot_name: str) -> dict:
    """Load state/*.json with file locking."""
    STATE_DIR.mkdir(exist_ok=True)
    path = STATE_DIR / f"{bot_name}.json"
    if not path.exists():
        return {}
    with file_lock(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


def save_state(bot_name: str, data: dict) -> None:
    """Save state/*.json with file locking."""
    STATE_DIR.mkdir(exist_ok=True)
    path = STATE_DIR / f"{bot_name}.json"
    with file_lock(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


# ── Logging Setup ─────────────────────────────────────────────────────────────

def setup_logging(name: str, level: str = "INFO") -> logging.Logger:
    """Consistent logging setup across all agents."""
    log = logging.getLogger(name)
    if not log.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        log.addHandler(handler)
    log.setLevel(getattr(logging, level.upper(), logging.INFO))
    return log


# ── API Helpers ───────────────────────────────────────────────────────────────

def pionex_sign(method: str, path: str, params: dict, secret: str) -> tuple[str, str]:
    """HMAC SHA256 signing for Pionex API.

    Returns (query_string_with_signature, signature).
    Does NOT mutate the *params* dict.
    """
    ts = str(int(time.time() * 1000))
    p = {**params, "timestamp": ts}
    qs = "&".join(f"{k}={v}" for k, v in sorted(p.items()))
    sign_str = f"{method}{path}?{qs}"
    sig = hmac.new(secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    return qs + f"&signature={sig}", sig
