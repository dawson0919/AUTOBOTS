"""Multi-timeframe confirmation for 三刀流 signals."""
from __future__ import annotations

import logging

from signal_v6 import calc_raw_signal, sma, SIG_DIR, SIG_HOLD

log = logging.getLogger("mtf")


def fetch_4h_signal(client, symbol, lb_period, gy_period, zf_period, dist_pct, disable_bounce):
    """Fetch 4H klines and compute current signal direction."""
    try:
        # Fetch 4H klines
        params = {"symbol": symbol, "interval": "4H", "limit": "500"}
        resp = client._get("/api/v1/market/klines", params)
        klines = resp.get("data", {}).get("klines", [])
        if not klines:
            return 0  # unknown, don't block

        # API returns newest first, reverse
        klines.reverse()
        closes = [float(k["close"]) for k in klines]

        if len(closes) < lb_period:
            return 0

        price = closes[-1]
        lb_val = sma(closes, lb_period)
        gy_val = sma(closes, gy_period)
        zf_val = sma(closes, zf_period)

        if not lb_val or not gy_val or not zf_val:
            return 0

        raw = calc_raw_signal(price, lb_val, gy_val, zf_val, dist_pct, disable_bounce)
        return SIG_DIR.get(raw, 0)
    except Exception as e:
        log.warning("[MTF] Failed to fetch 4H for %s: %s", symbol, e)
        return 0  # don't block on failure


def confirm_flip(client, symbol, new_1h_dir, lb, gy, zf, dist_pct, disable_bounce):
    """Check if 4H direction agrees with 1H flip direction.

    Returns:
        True if 4H confirms (or is neutral/unavailable -- don't block)
        False if 4H contradicts the 1H signal
    """
    dir_4h = fetch_4h_signal(client, symbol, lb, gy, zf, dist_pct, disable_bounce)

    if dir_4h == 0:
        log.info("[MTF] %s: 4H is NEUTRAL -- allowing 1H flip", symbol)
        return True  # neutral = don't block

    if dir_4h == new_1h_dir:
        log.info("[MTF] %s: 4H CONFIRMS %s -- flip approved", symbol,
                "LONG" if new_1h_dir == 1 else "SHORT")
        return True

    log.warning("[MTF] %s: 4H CONTRADICTS -- 1H wants %s but 4H says %s -- BLOCKING flip",
               symbol, "LONG" if new_1h_dir == 1 else "SHORT",
               "LONG" if dir_4h == 1 else "SHORT")
    return False
