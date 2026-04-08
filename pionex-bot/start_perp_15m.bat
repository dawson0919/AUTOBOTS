@echo off
setlocal
cd /d "%~dp0"
title Pionex 15M Perp Manager

:: Set any required environment variables
:: set TELEGRAM_BOT_TOKEN=...
:: set TELEGRAM_CHAT_ID=...

python perp_manager.py --config perp_bots_15m.toml --loop

pause
