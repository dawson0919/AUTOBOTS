import io
import logging
import sys
from config import Config


def setup_logger(name: str = "pionex-bot") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, Config.LOG_LEVEL, logging.INFO))

    if not logger.handlers:
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        # Use UTF-8 stdout wrapper to avoid cp950 encoding errors on Windows
        utf8_stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        console = logging.StreamHandler(utf8_stdout)
        console.setFormatter(fmt)
        logger.addHandler(console)

        file_handler = logging.FileHandler("bot.log", encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

    return logger
