@echo off
title Stop TradingOS V2
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Stop-TradingOS.ps1"
pause
