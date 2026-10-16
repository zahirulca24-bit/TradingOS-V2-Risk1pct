@echo off
title TradingOS V2 - Auto Scanner
cd /d "%~dp0"
echo ========================================================
echo   TradingOS V2 - Automated 15-Minute Background Scanner
echo   Scans automatically on every 15-minute candle close.
echo   Syncs results directly to Neon Database.
echo   Keep this window open or minimized.
echo   Press Ctrl+C to stop.
echo ========================================================
.venv\Scripts\python.exe backend\scanner_scheduler.py
pause
