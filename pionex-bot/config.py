import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # API credentials
    API_KEY: str = os.getenv("PIONEX_API_KEY", "")
    API_SECRET: str = os.getenv("PIONEX_API_SECRET", "")

    # Trading
    SYMBOL: str = os.getenv("SYMBOL", "BTC_USDT_PERP")
    FAST_MA_PERIOD: int = int(os.getenv("FAST_MA_PERIOD", "7"))
    SLOW_MA_PERIOD: int = int(os.getenv("SLOW_MA_PERIOD", "25"))
    KLINE_INTERVAL: str = os.getenv("KLINE_INTERVAL", "15M")

    # Risk management
    MAX_POSITION_SIZE: float = float(os.getenv("MAX_POSITION_SIZE", "0.01"))
    STOP_LOSS_PCT: float = float(os.getenv("STOP_LOSS_PCT", "2.0"))
    TAKE_PROFIT_PCT: float = float(os.getenv("TAKE_PROFIT_PCT", "4.0"))
    MAX_DAILY_LOSS_PCT: float = float(os.getenv("MAX_DAILY_LOSS_PCT", "5.0"))

    # System
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    DRY_RUN: bool = os.getenv("DRY_RUN", "true").lower() == "true"

    # API
    BASE_URL: str = "https://api.pionex.com"
    WS_PUBLIC_URL: str = "wss://ws.pionex.com/wsPub"
    WS_PRIVATE_URL: str = "wss://ws.pionex.com/ws"

    # ── Triple Blade (三刀流) Bot Config ──────────────────────
    BLADE_SYMBOL: str = os.getenv("BLADE_SYMBOL", "ETH_USDT_PERP")
    BLADE_BASE: str = os.getenv("BLADE_BASE", "ETH.PERP")
    BLADE_QUOTE: str = os.getenv("BLADE_QUOTE", "USDT")
    BLADE_MA_FAST: int = int(os.getenv("BLADE_MA_FAST", "7"))
    BLADE_MA_MID: int = int(os.getenv("BLADE_MA_MID", "25"))
    BLADE_MA_SLOW: int = int(os.getenv("BLADE_MA_SLOW", "99"))
    BLADE_INTERVAL: str = os.getenv("BLADE_INTERVAL", "15M")
    BLADE_LEVERAGE: int = int(os.getenv("BLADE_LEVERAGE", "5"))
    BLADE_INVESTMENT: str = os.getenv("BLADE_INVESTMENT", "50")  # USDT per grid bot
    BLADE_GRID_COUNT: int = int(os.getenv("BLADE_GRID_COUNT", "10"))
    BLADE_GRID_TYPE: str = os.getenv("BLADE_GRID_TYPE", "arithmetic")
    BLADE_RANGE_PCT: float = float(os.getenv("BLADE_RANGE_PCT", "3.0"))  # ±% from current price
    BLADE_MIN_STRENGTH: int = int(os.getenv("BLADE_MIN_STRENGTH", "2"))  # min signal strength to act (1-3)
    BLADE_POLL_SEC: int = int(os.getenv("BLADE_POLL_SEC", "60"))  # seconds between checks
    BLADE_LOSS_STOP_TYPE: str = os.getenv("BLADE_LOSS_STOP_TYPE", "profit_ratio")
    BLADE_LOSS_STOP: str = os.getenv("BLADE_LOSS_STOP", "-0.15")  # -15% loss stop
    BLADE_PROFIT_STOP_TYPE: str = os.getenv("BLADE_PROFIT_STOP_TYPE", "profit_ratio")
    BLADE_PROFIT_STOP: str = os.getenv("BLADE_PROFIT_STOP", "0.30")  # +30% take profit
