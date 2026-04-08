"""Pionex REST API client with HMAC SHA256 authentication."""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from config import Config
from logger import setup_logger

log = setup_logger("client")


class PionexClient:
    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or Config()
        self._http = httpx.Client(base_url=self.cfg.BASE_URL, timeout=10)
        self._time_offset: int = 0  # ms offset between local and server time
        self._sync_server_time()

    def _sync_server_time(self):
        """Sync local clock with Pionex server to avoid INVALID_TIMESTAMP."""
        try:
            local_ts = int(time.time() * 1000)
            resp = self._http.get("/api/v1/common/symbols?symbols=BTC_USDT")
            server_ts = resp.json().get("timestamp", local_ts)
            self._time_offset = server_ts - local_ts
            if abs(self._time_offset) > 1000:
                log.info("Time offset synced: %dms", self._time_offset)
        except Exception as e:
            log.warning("Failed to sync server time: %s", e)
            self._time_offset = 0

    # ── Authentication ─────────────────────────────────────────────

    def _sign(self, method: str, path: str, params: dict, body: str = "") -> dict:
        """Generate PIONEX-SIGNATURE header using HMAC SHA256."""
        ts = str(int(time.time() * 1000) + self._time_offset)
        params["timestamp"] = ts

        # Sort params alphabetically and build query string
        sorted_params = sorted(params.items())
        query = "&".join(f"{k}={v}" for k, v in sorted_params)
        path_url = f"{path}?{query}"

        # Build signing string
        sign_str = f"{method}{path_url}"
        if body:
            sign_str += body

        signature = hmac.new(
            self.cfg.API_SECRET.encode(), sign_str.encode(), hashlib.sha256
        ).hexdigest()

        return {
            "PIONEX-KEY": self.cfg.API_KEY,
            "PIONEX-SIGNATURE": signature,
        }

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        body: dict | None = None,
        signed: bool = False,
    ) -> dict[str, Any]:
        params = params or {}
        json_body = None
        body_str = ""

        if body:
            import json as _json
            body_str = _json.dumps(body, separators=(",", ":"))
            json_body = body

        headers = {}
        if signed:
            headers = self._sign(method, path, params, body_str)

        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        url = f"{path}?{query}" if query else path

        resp = self._http.request(
            method,
            url,
            headers=headers,
            json=json_body if method != "GET" else None,
        )
        data = resp.json()

        if not data.get("result", False):
            code = data.get("code", "UNKNOWN")
            msg = data.get("message", "Unknown error")
            log.error("API error: %s - %s [%s %s]", code, msg, method, path)
            raise PionexAPIError(code, msg)

        return data.get("data", {})

    # ── Public endpoints ───────────────────────────────────────────

    def get_symbols(self, symbol: str | None = None, market_type: str = "PERP") -> list[dict]:
        params: dict[str, str] = {}
        if symbol:
            params["symbols"] = symbol
        else:
            params["type"] = market_type
        return self._request("GET", "/api/v1/common/symbols", params).get("symbols", [])

    def get_klines(
        self, symbol: str, interval: str, limit: int = 200, end_time: int | None = None
    ) -> list[dict]:
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": str(limit)}
        if end_time:
            params["endTime"] = str(end_time)
        return self._request("GET", "/api/v1/market/klines", params).get("klines", [])

    def get_ticker(self, symbol: str) -> dict:
        data = self._request("GET", "/api/v1/market/tickers", {"symbol": symbol})
        tickers = data.get("tickers", [])
        return tickers[0] if tickers else {}

    def get_book_ticker(self, symbol: str) -> dict:
        data = self._request("GET", "/api/v1/market/bookTickers", {"symbol": symbol})
        tickers = data.get("tickers", [])
        return tickers[0] if tickers else {}

    def get_depth(self, symbol: str, limit: int = 20) -> dict:
        return self._request("GET", "/api/v1/market/depth", {"symbol": symbol, "limit": str(limit)})

    def get_trades(self, symbol: str, limit: int = 100) -> list[dict]:
        return self._request(
            "GET", "/api/v1/market/trades", {"symbol": symbol, "limit": str(limit)}
        ).get("trades", [])

    # ── Private: Account ───────────────────────────────────────────

    def get_balance(self) -> list[dict]:
        return self._request("GET", "/api/v1/account/balances", signed=True).get("balances", [])

    # ── Private: Orders ────────────────────────────────────────────

    def new_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        size: str | None = None,
        price: str | None = None,
        amount: str | None = None,
        ioc: bool = False,
        client_order_id: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {"symbol": symbol, "side": side, "type": order_type}
        if size:
            body["size"] = size
        if price:
            body["price"] = price
        if amount:
            body["amount"] = amount
        if ioc:
            body["IOC"] = True
        if client_order_id:
            body["clientOrderId"] = client_order_id

        return self._request("POST", "/api/v1/trade/order", body=body, signed=True)

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        return self._request(
            "DELETE",
            "/api/v1/trade/order",
            body={"symbol": symbol, "orderId": order_id},
            signed=True,
        )

    def cancel_all_orders(self, symbol: str) -> dict:
        return self._request(
            "DELETE",
            "/api/v1/trade/allOrders",
            body={"symbol": symbol},
            signed=True,
        )

    def get_open_orders(self, symbol: str) -> list[dict]:
        return self._request(
            "GET", "/api/v1/trade/openOrders", {"symbol": symbol}, signed=True
        ).get("orders", [])

    def get_all_orders(self, symbol: str, limit: int = 100) -> list[dict]:
        return self._request(
            "GET",
            "/api/v1/trade/allOrders",
            {"symbol": symbol, "limit": str(limit)},
            signed=True,
        ).get("orders", [])

    def get_fills(self, symbol: str, limit: int = 100) -> list[dict]:
        return self._request(
            "GET",
            "/api/v1/trade/fills",
            {"symbol": symbol, "limit": str(limit)},
            signed=True,
        ).get("fills", [])

    # ── Convenience: Spot ─────────────────────────────────────────

    def market_buy(self, symbol: str, amount: str) -> dict:
        return self.new_order(symbol, "BUY", "MARKET", amount=amount)

    def market_sell(self, symbol: str, size: str) -> dict:
        return self.new_order(symbol, "SELL", "MARKET", size=size)

    def limit_buy(self, symbol: str, price: str, size: str) -> dict:
        return self.new_order(symbol, "BUY", "LIMIT", size=size, price=price)

    def limit_sell(self, symbol: str, price: str, size: str) -> dict:
        return self.new_order(symbol, "SELL", "LIMIT", size=size, price=price)

    # ── Futures (PERP): Account & Positions (/uapi/v1/) ──────────

    def get_futures_balance(self) -> dict:
        """Get futures account balances. Returns {balances: [...], isolates: [...]}."""
        return self._request("GET", "/uapi/v1/account/balances", signed=True)

    def get_futures_detail(self) -> dict:
        """Get futures account detail (balances, positions, risk state)."""
        return self._request("GET", "/uapi/v1/account/detail", signed=True)

    def get_active_positions(self, symbol: str | None = None) -> list[dict]:
        """Get active futures positions."""
        params: dict[str, str] = {}
        if symbol:
            params["symbol"] = symbol
        return self._request("GET", "/uapi/v1/account/positions", params, signed=True).get("positions", [])

    def get_leverage(self, symbol: str | None = None) -> list[dict]:
        """Get leverage settings for futures symbols."""
        params: dict[str, str] = {}
        if symbol:
            params["symbol"] = symbol
        return self._request("GET", "/uapi/v1/account/leverage", params, signed=True).get("leverages", [])

    def modify_leverage(self, symbol: str, leverage: int) -> dict:
        """Modify leverage for a futures symbol."""
        return self._request(
            "POST", "/uapi/v1/account/leverage",
            body={"symbol": symbol, "leverage": str(leverage)},
            signed=True,
        )

    def get_position_mode(self) -> str:
        """Get position mode: BUYSELL (hedge) or NETMODE (one-way)."""
        return self._request("GET", "/uapi/v1/account/positionMode", signed=True).get("positionMode", "")

    # ── Futures (PERP): Orders ─────────────────────────────────────

    def new_futures_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        size: str | None = None,
        price: str | None = None,
        amount: str | None = None,
        ioc: bool = False,
        client_order_id: str | None = None,
    ) -> dict:
        """Place a futures order via /uapi/v1/trade/order."""
        body: dict[str, Any] = {"symbol": symbol, "side": side, "type": order_type}
        if size:
            body["size"] = size
        if price:
            body["price"] = price
        if amount:
            body["amount"] = amount
        if ioc:
            body["IOC"] = True
        if client_order_id:
            body["clientOrderId"] = client_order_id
        return self._request("POST", "/uapi/v1/trade/order", body=body, signed=True)

    def cancel_futures_order(self, symbol: str, order_id: str) -> dict:
        return self._request(
            "DELETE", "/uapi/v1/trade/order",
            body={"symbol": symbol, "orderId": order_id},
            signed=True,
        )

    def cancel_all_futures_orders(self, symbol: str) -> dict:
        return self._request(
            "DELETE", "/uapi/v1/trade/allOrders",
            body={"symbol": symbol},
            signed=True,
        )

    def get_futures_open_orders(self, symbol: str) -> list[dict]:
        return self._request(
            "GET", "/uapi/v1/trade/openOrders", {"symbol": symbol}, signed=True
        ).get("orders", [])

    def get_futures_all_orders(self, symbol: str, limit: int = 100) -> list[dict]:
        return self._request(
            "GET", "/uapi/v1/trade/allOrders",
            {"symbol": symbol, "limit": str(limit)},
            signed=True,
        ).get("orders", [])

    # ── Futures Convenience ────────────────────────────────────────

    def futures_market_buy(self, symbol: str, size: str) -> dict:
        """Open long / close short with market order."""
        return self.new_futures_order(symbol, "BUY", "MARKET", size=size)

    def futures_market_sell(self, symbol: str, size: str) -> dict:
        """Open short / close long with market order."""
        return self.new_futures_order(symbol, "SELL", "MARKET", size=size)

    # ── Bot API: Futures Grid ─────────────────────────────────────

    def bot_futures_grid_create(
        self,
        base: str,
        quote: str,
        top: str,
        bottom: str,
        row: int,
        grid_type: str,
        trend: str,
        leverage: int,
        quote_investment: str,
        loss_stop_type: str | None = None,
        loss_stop: str | None = None,
        profit_stop_type: str | None = None,
        profit_stop: str | None = None,
    ) -> dict:
        """Create a futures grid bot via /api/v1/bot/orders/futuresGrid/create.

        Args:
            base: e.g. "ETH.PERP"
            quote: e.g. "USDT"
            top/bottom: price range as strings
            row: number of grid lines (3-200)
            grid_type: "arithmetic" or "geometric"
            trend: "long", "short", or "no_trend"
            leverage: leverage multiplier (1-100)
            quote_investment: investment amount in quote currency
        """
        bu_order_data: dict[str, Any] = {
            "top": top,
            "bottom": bottom,
            "row": row,
            "grid_type": grid_type,
            "trend": trend,
            "leverage": leverage,
            "quoteInvestment": quote_investment,
        }
        if loss_stop_type:
            bu_order_data["lossStopType"] = loss_stop_type
        if loss_stop:
            bu_order_data["lossStop"] = loss_stop
        if profit_stop_type:
            bu_order_data["profitStopType"] = profit_stop_type
        if profit_stop:
            bu_order_data["profitStop"] = profit_stop

        body = {
            "base": base,
            "quote": quote,
            "buOrderData": bu_order_data,
        }
        return self._request("POST", "/api/v1/bot/orders/futuresGrid/create", body=body, signed=True)

    def bot_futures_grid_cancel(self, bu_order_id: str) -> dict:
        """Cancel a futures grid bot."""
        body: dict[str, Any] = {"buOrderId": bu_order_id}
        return self._request("POST", "/api/v1/bot/orders/futuresGrid/cancel", body=body, signed=True)

    def bot_futures_grid_get(self, bu_order_id: str) -> dict:
        """Get a futures grid bot order by buOrderId."""
        return self._request(
            "GET", "/api/v1/bot/orders/futuresGrid/order",
            {"buOrderId": bu_order_id}, signed=True,
        )

    def close(self):
        self._http.close()


class PionexAPIError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")
