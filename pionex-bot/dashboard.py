"""Dashboard server for Autobots trading system.

Requirements: pip install flask httpx
"""
import sys
import os
import json
import time
import secrets

sys.stdout.reconfigure(encoding="utf-8")

from flask import Flask, Response, jsonify, request, send_file
import httpx

from utils import load_toml, BOT_DIR, STATE_DIR, TOML_PATH

PIONEX_BASE = "https://api.pionex.com/api/v1"
ROOT_DIR = BOT_DIR.parent

app = Flask(__name__)

# ── Auth ──────────────────────────────────────────────────────────────────────

AUTH_TOKEN = os.getenv("DASHBOARD_TOKEN", "autobots-2026")


def check_auth() -> bool:
    """Check Authorization header or query param."""
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        token = request.args.get("token", "")
    return secrets.compare_digest(token, AUTH_TOKEN)


@app.before_request
def auth_middleware():
    # Skip auth for static HTML pages and public proxy endpoints
    if request.path in _HTML_PAGES or request.path == "/favicon.ico":
        return
    if request.path == "/nba_data.json":
        return
    # Public read-only proxy endpoints (Polymarket, ESPN, Pionex market data)
    PUBLIC_PREFIXES = ("/api/poly/", "/api/nba/", "/api/tickers", "/api/klines", "/api/stream",
                       "/api/bots", "/api/pnl", "/api/state", "/api/portfolio", "/api/qsignals",
                       "/api/mlb/")
    if any(request.path.startswith(p) for p in PUBLIC_PREFIXES):
        return
    # Sensitive endpoints require auth token
    if request.path.startswith("/api/"):
        if not check_auth():
            return jsonify({"error": "unauthorized — pass ?token= or Authorization header"}), 401


# ── Routes ───────────────────────────────────────────────────────────────────

# ── Static page helper ────────────────────────────────────────────────────────

def _serve_html(filename):
    path = ROOT_DIR / filename
    if path.exists():
        return send_file(str(path))
    return f"<h1>{filename} not found</h1>", 404

_HTML_PAGES = {
    "/": "index.html",
    "/polymarket": "polymarket.html",
    "/nba": "nba.html",
    "/btc": "btc.html",
    "/eth": "eth.html",
    "/wti": "wti.html",
    "/mlb": "mlb.html",
}

# Dynamically register all HTML page routes
def _make_handler(filename):
    def handler():
        return _serve_html(filename)
    return handler

for _path, _file in _HTML_PAGES.items():
    _endpoint = "page_" + _file.replace(".", "_")
    app.add_url_rule(_path, _endpoint, _make_handler(_file))

@app.route("/nba_data.json")
def page_nba_data():
    path = ROOT_DIR / "nba_data.json"
    if path.exists():
        return send_file(str(path), mimetype="application/json")
    return jsonify({"error": "no data"}), 404




@app.route("/api/nba/predictions")
def nba_predictions():
    """Run NBA predictor and return JSON results."""
    import subprocess
    try:
        r = subprocess.run(
            [sys.executable, str(BOT_DIR / "nba_predictor.py"), "--edge", "--json"],
            capture_output=True, text=True, timeout=60, encoding="utf-8"
        )
        if r.returncode == 0:
            return Response(r.stdout, content_type="application/json")
        return jsonify({"error": r.stderr[:500]}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/mlb/predictions")
def mlb_predictions():
    """Run MLB predictor and return JSON results."""
    import subprocess
    try:
        r = subprocess.run(
            [sys.executable, str(ROOT_DIR / "MLB" / "mlb_predictor.py"), "--json"],
            capture_output=True, text=True, timeout=180, encoding="utf-8"
        )
        if r.returncode == 0:
            return Response(r.stdout, content_type="application/json")
        return jsonify({"error": r.stderr[:500]}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route("/api/nba/odds", methods=["GET", "POST"])
def nba_odds():
    """Read/write Taiwan sports lottery odds."""
    odds_path = BOT_DIR / "nba_odds.json"
    if request.method == "POST":
        try:
            data = request.get_json(force=True)
            # Merge with existing
            existing = {}
            if odds_path.exists():
                with open(odds_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            odds = existing.get("odds", {})
            # data format: { "key": "Away @ Home", "spread": -3.5, "ou": 226.5 }
            if "key" in data:
                odds[data["key"]] = {
                    "spread": float(data.get("spread", 0)),
                    "ou": float(data.get("ou", 0)),
                    "updated": data.get("updated", ""),
                }
            elif "odds" in data:
                # Bulk update
                odds.update(data["odds"])
            existing["odds"] = odds
            with open(odds_path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)
            return jsonify({"ok": True, "count": len(odds)})
        except Exception as e:
            return jsonify({"error": str(e)}), 400
    else:
        if odds_path.exists():
            with open(odds_path, "r", encoding="utf-8") as f:
                return jsonify(json.load(f))
        return jsonify({"odds": {}})


@app.route("/api/nba/scoreboard")
def nba_scoreboard():
    """Proxy ESPN NBA live scoreboard."""
    try:
        r = httpx.get(
            "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
            timeout=10,
        )
        return Response(r.content, status=r.status_code, content_type="application/json")
    except Exception as e:
        return jsonify({"error": str(e)}), 502


# ── Polymarket API Proxy (solves CORS) ───────────────────────────────────────

@app.route("/api/poly/markets")
def poly_markets():
    """Proxy Polymarket Gamma API markets."""
    try:
        params = {k: v for k, v in request.args.items()}
        r = httpx.get("https://gamma-api.polymarket.com/markets", params=params, timeout=15)
        return Response(r.content, status=r.status_code,
                       content_type="application/json",
                       headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/poly/events")
def poly_events():
    """Proxy Polymarket Gamma API events."""
    try:
        params = {k: v for k, v in request.args.items()}
        r = httpx.get("https://gamma-api.polymarket.com/events", params=params, timeout=15)
        return Response(r.content, status=r.status_code,
                       content_type="application/json",
                       headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/tickers")
def tickers():
    """Proxy GET /api/tickers -> Pionex PERP tickers."""
    try:
        r = httpx.get(
            f"{PIONEX_BASE}/market/tickers",
            params={"type": "PERP"},
            timeout=10,
        )
        r.raise_for_status()
        return Response(r.content, status=r.status_code, mimetype="application/json")
    except httpx.HTTPError as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/klines")
def klines():
    """Proxy GET /api/klines?symbol=X&interval=60M&limit=500 -> Pionex klines."""
    symbol = request.args.get("symbol", "")
    interval = request.args.get("interval", "60M")
    limit = request.args.get("limit", "500")
    if not symbol:
        return jsonify({"error": "symbol parameter required"}), 400
    try:
        r = httpx.get(
            f"{PIONEX_BASE}/market/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=15,
        )
        r.raise_for_status()
        return Response(r.content, status=r.status_code, mimetype="application/json")
    except httpx.HTTPError as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/bots")
def bots():
    """Expose bot configuration from bots.toml."""
    if not TOML_PATH.exists():
        return jsonify({"error": "bots.toml not found"}), 404
    try:
        cfg = load_toml(str(TOML_PATH))
        return jsonify(cfg)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/qsignals")
def qsignals():
    """Latest Q-SIGNALS consensus per bot (from signal_manager_qsignals.py JSONL log)."""
    jsonl = STATE_DIR / "qsignals_compare.jsonl"
    if not jsonl.exists():
        return jsonify({"bots": {}, "error": "no qsignals data yet"})
    latest: dict[str, dict] = {}
    try:
        with open(jsonl, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                latest[rec["bot"]] = rec
        return jsonify({"bots": latest})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/qsignals-bots")
def qsignals_bots():
    """Virtual Q-SIGNALS grid bots: merge bots_qsignals.toml config + state/qs_*.json."""
    toml_path = BOT_DIR / "bots_qsignals.toml"
    if not toml_path.exists():
        return jsonify({"bots": {}, "error": "bots_qsignals.toml not found"})
    try:
        cfg = load_toml(str(toml_path))
        bots = cfg.get("bots", {})
        result = {}
        for name, bcfg in bots.items():
            state_file = STATE_DIR / f"{name}.json"
            state = {}
            if state_file.exists():
                try:
                    state = json.loads(state_file.read_text(encoding="utf-8"))
                except Exception:
                    pass
            result[name] = {"config": bcfg, "state": state}
        return jsonify({"bots": result, "dry_run": cfg.get("global", {}).get("dry_run", True)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/state")
def state():
    """Expose all bot state files from state/*.json."""
    if not STATE_DIR.exists():
        return jsonify({})
    result = {}
    for json_file in sorted(STATE_DIR.glob("*.json")):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                result[json_file.stem] = json.load(f)
        except Exception as e:
            result[json_file.stem] = {"error": str(e)}
    return jsonify(result)


@app.route("/api/portfolio")
def portfolio():
    """Expose portfolio agent state."""
    path = STATE_DIR / "portfolio.json"
    if not path.exists():
        return jsonify({"error": "portfolio agent not running", "allocations": {}}), 200
    with open(path, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/api/stream")
def stream():
    """SSE endpoint: pushes Pionex ticker data every 5 seconds."""
    def generate():
        while True:
            try:
                r = httpx.get(
                    f"{PIONEX_BASE}/market/tickers",
                    params={"type": "PERP"},
                    timeout=10,
                )
                data = r.json()
                yield f"data: {json.dumps(data)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            time.sleep(5)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── P&L Endpoint ─────────────────────────────────────────────────────────────

@app.route("/api/pnl")
def pnl():
    """Fetch P&L for all bots from Pionex Bot API."""
    import hmac, hashlib

    config_path = os.path.expanduser("~/.pionex/config.toml")
    api_key = secret = ""
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as f:
            for line in f:
                if "api_key" in line and "=" in line:
                    api_key = line.split("=", 1)[1].strip().strip('"')
                if "secret_key" in line and "=" in line:
                    secret = line.split("=", 1)[1].strip().strip('"')

    if not api_key or not secret:
        return jsonify({"error": "no API keys configured"}), 500

    # Get Pionex server time offset (local clock may be off)
    try:
        st = httpx.get(f"{PIONEX_BASE}/market/tickers?type=PERP&limit=1", timeout=5)
        time_offset = st.json().get("timestamp", int(time.time()*1000)) - int(time.time()*1000)
    except Exception:
        time_offset = 0

    def bot_get(bid):
        path = "/api/v1/bot/orders/futuresGrid/order"
        ts = str(int(time.time() * 1000) + time_offset)
        params = {"buOrderId": bid, "timestamp": ts}
        qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        sig = hmac.new(secret.encode(), f"GET{path}?{qs}".encode(), hashlib.sha256).hexdigest()
        url = f"https://api.pionex.com{path}?{qs}&signature={sig}"
        headers = {"PIONEX-KEY": api_key, "PIONEX-SIGNATURE": sig}
        r = httpx.get(url, headers=headers, timeout=10)
        data = r.json()
        if data.get("result"):
            return data.get("data", {})
        return None

    bots_cfg = load_toml(str(TOML_PATH)).get("bots", {})
    results = {}
    total_invest = 0.0
    total_net = 0.0

    for name, cfg in bots_cfg.items():
        bid = cfg.get("bu_order_id", "")
        if not bid:
            state_path = STATE_DIR / f"{name}.json"
            if state_path.exists():
                with open(state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
                bid = state.get("bu_order_id", "")
        if not bid:
            continue

        try:
            data = bot_get(bid)
            if not data:
                continue
            od = data.get("buOrderData", {})

            invest = float(od.get("initQuoteInvestment", 0) or 0)
            margin = float(od.get("marginBalance", 0) or 0)
            grid_pnl = float(od.get("gridProfit", 0) or 0)
            real_pnl = float(od.get("totalRealizedProfit", 0) or 0)
            funding = float(od.get("totalFundingFee", 0) or 0)
            fee = float(od.get("totalFee", 0) or 0)
            net = margin - invest
            roi = net / invest * 100 if invest > 0 else 0

            total_invest += invest
            total_net += net

            results[name] = {
                "symbol": cfg.get("symbol", ""),
                "trend": od.get("trend", "?"),
                "status": od.get("status", "?"),
                "investment": invest,
                "margin": margin,
                "grid_pnl": round(grid_pnl, 4),
                "real_pnl": round(real_pnl, 4),
                "funding": round(funding, 4),
                "fee": round(fee, 4),
                "net_pnl": round(net, 2),
                "roi": round(roi, 2),
            }
        except Exception as e:
            import traceback
            results[name] = {"error": str(e), "trace": traceback.format_exc()[:200]}
            continue

    return jsonify({
        "bots": results,
        "total_investment": round(total_invest, 2),
        "total_net_pnl": round(total_net, 2),
        "total_roi": round(total_net / total_invest * 100, 2) if total_invest > 0 else 0,
    })


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Autobots dashboard server")
    parser.add_argument("--port", type=int, default=5000, help="Port to listen on (default: 5000)")
    parser.add_argument("--public", action="store_true", help="Bind to 0.0.0.0 (default: localhost only)")
    args = parser.parse_args()

    host = "0.0.0.0" if args.public or os.getenv("PORT") else "127.0.0.1"
    port = int(os.getenv("PORT", args.port))

    print(f"Dashboard running at: http://{'localhost' if host == '127.0.0.1' else host}:{port}")
    print(f"  Serving index.html from: {ROOT_DIR / 'index.html'}")
    print(f"  Bot config: {TOML_PATH}")
    print(f"  State dir:  {STATE_DIR}")
    print(f"  Auth: sensitive APIs require token (DASHBOARD_TOKEN env or default)")
    print(f"  Bind: {host} ({'public' if host == '0.0.0.0' else 'local only — use --public for external access'})")
    app.run(host=host, port=port, debug=False)
