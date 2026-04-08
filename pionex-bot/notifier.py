"""Telegram notification module for Autobots trading system."""
import os, sys, json, logging
from datetime import datetime, timezone

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

log = logging.getLogger("notifier")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8674413708:AAGZLb6DnATH_bCqWJopciParrWUwjMWFuM")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "1062186549")

class Notifier:
    """Send notifications via Telegram Bot API."""

    def __init__(self, token: str = "", chat_id: str = ""):
        self.token = token or TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or TELEGRAM_CHAT_ID
        self.enabled = bool(self.token and self.chat_id)
        if not self.enabled:
            log.info("Telegram notifications disabled (no TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID)")

    def send(self, message: str, parse_mode: str = "HTML"):
        """Send a message via Telegram."""
        if not self.enabled:
            return
        try:
            import httpx
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            resp = httpx.post(url, json={
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": parse_mode,
            }, timeout=10)
            if resp.status_code != 200:
                log.warning("Telegram send failed: %s", resp.text[:200])
        except Exception as e:
            log.warning("Telegram error: %s", e)

    def notify_flip(self, bot_name: str, old_dir: str, new_dir: str, price: float, symbol: str):
        """Notify signal flip."""
        emoji = "🟢" if new_dir == "LONG" else "🔴"
        self.send(
            f"{emoji} <b>Signal Flip: {bot_name.upper()}</b>\n"
            f"Direction: {old_dir} → <b>{new_dir}</b>\n"
            f"Price: {price:,.4f}\n"
            f"Symbol: {symbol}\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )

    def notify_rebuild(self, bot_name: str, trend: str, reason: str):
        """Notify orphan bot rebuild."""
        self.send(
            f"🔧 <b>Bot Rebuilt: {bot_name.upper()}</b>\n"
            f"Direction: <b>{trend.upper()}</b>\n"
            f"Reason: {reason}\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )

    def notify_error(self, bot_name: str, error: str):
        """Notify error."""
        self.send(
            f"⚠️ <b>Error: {bot_name.upper()}</b>\n"
            f"{error}\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )

    def notify_risk(self, message: str):
        """Notify risk alert."""
        self.send(f"🚨 <b>Risk Alert</b>\n{message}")

    def notify_daily_summary(self, summary: dict):
        """Send daily summary."""
        lines = ["📊 <b>Daily Summary</b>"]
        lines.append(f"Portfolio: ${summary.get('portfolio_value', 0):,.2f}")
        lines.append(f"Drawdown: {summary.get('drawdown', 0):.1%}")
        lines.append("")
        for bot, info in summary.get("bots", {}).items():
            status = info.get("status", "?")
            roi = info.get("roi", 0)
            emoji = "✅" if status == "KEEP" else "🔄" if status == "FLIP" else "⚠️"
            lines.append(f"{emoji} {bot.upper()}: {roi:+.1%} ({status})")
        lines.append(f"\nTime: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        self.send("\n".join(lines))


# Singleton for easy import
_notifier = None

def get_notifier() -> Notifier:
    global _notifier
    if _notifier is None:
        _notifier = Notifier()
    return _notifier
