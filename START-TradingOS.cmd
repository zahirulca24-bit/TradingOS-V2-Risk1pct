@echo off
title TradingOS V2
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch-TradingOS.ps1"
pause
