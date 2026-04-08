"""Pionex WebSocket client for real-time market data and private streams."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any, Callable

import websockets

from config import Config
from logger import setup_logger

log = setup_logger("ws")


class PionexWebSocket:
    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or Config()
        self._ws = None
        self._running = False
        self._callbacks: dict[str, list[Callable]] = {}
        self._ping_task: asyncio.Task | None = None

    # ── Authentication ─────────────────────────────────────────────

    def _build_private_url(self) -> str:
        ts = str(int(time.time() * 1000))
        path_url = f"/ws?timestamp={ts}"
        sign_str = f"websocket_auth{path_url}"
        signature = hmac.new(
            self.cfg.API_SECRET.encode(), sign_str.encode(), hashlib.sha256
        ).hexdigest()
        return f"{self.cfg.WS_PRIVATE_URL}?key={self.cfg.API_KEY}&timestamp={ts}&signature={signature}"

    # ── Connection ─────────────────────────────────────────────────

    async def connect_public(self):
        self._ws = await websockets.connect(self.cfg.WS_PUBLIC_URL)
        self._running = True
        log.info("Connected to public WebSocket")

    async def connect_private(self):
        url = self._build_private_url()
        self._ws = await websockets.connect(url)
        self._running = True
        log.info("Connected to private WebSocket")

    async def disconnect(self):
        self._running = False
        if self._ping_task:
            self._ping_task.cancel()
        if self._ws:
            await self._ws.close()
            log.info("WebSocket disconnected")

    # ── Subscribe / Unsubscribe ────────────────────────────────────

    async def subscribe(self, topic: str, symbol: str, callback: Callable):
        key = f"{topic}:{symbol}"
        self._callbacks.setdefault(key, []).append(callback)

        msg = json.dumps({"op": "SUBSCRIBE", "topic": topic, "symbol": symbol})
        await self._ws.send(msg)
        log.info("Subscribed to %s %s", topic, symbol)

    async def unsubscribe(self, topic: str, symbol: str):
        key = f"{topic}:{symbol}"
        self._callbacks.pop(key, None)

        msg = json.dumps({"op": "UNSUBSCRIBE", "topic": topic, "symbol": symbol})
        await self._ws.send(msg)
        log.info("Unsubscribed from %s %s", topic, symbol)

    # ── Message loop ───────────────────────────────────────────────

    async def listen(self):
        self._ping_task = asyncio.create_task(self._heartbeat())

        while self._running:
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=30)
                data = json.loads(raw)

                # Handle PING
                if data.get("op") == "PING":
                    await self._ws.send(json.dumps({"op": "PONG", "timestamp": data.get("timestamp")}))
                    continue

                # Route to callbacks
                topic = data.get("topic", "")
                symbol = data.get("symbol", "")
                key = f"{topic}:{symbol}"
                for cb in self._callbacks.get(key, []):
                    try:
                        result = cb(data)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:
                        log.exception("Callback error for %s", key)

            except asyncio.TimeoutError:
                continue
            except websockets.ConnectionClosed:
                log.warning("WebSocket connection closed, reconnecting...")
                await self._reconnect()
            except Exception:
                log.exception("WebSocket listen error")
                await asyncio.sleep(1)

    async def _heartbeat(self):
        while self._running:
            try:
                await asyncio.sleep(10)
                if self._ws and self._ws.open:
                    await self._ws.send(json.dumps({"op": "PONG", "timestamp": str(int(time.time() * 1000))}))
            except Exception:
                pass

    async def _reconnect(self):
        await asyncio.sleep(3)
        try:
            if self._ws:
                await self._ws.close()
            # Reconnect to public by default; override for private
            self._ws = await websockets.connect(self.cfg.WS_PUBLIC_URL)
            log.info("Reconnected to WebSocket")

            # Resubscribe all topics
            for key in self._callbacks:
                topic, symbol = key.split(":", 1)
                msg = json.dumps({"op": "SUBSCRIBE", "topic": topic, "symbol": symbol})
                await self._ws.send(msg)
                log.info("Resubscribed to %s %s", topic, symbol)
        except Exception:
            log.exception("Reconnect failed, retrying in 5s")
            await asyncio.sleep(5)
