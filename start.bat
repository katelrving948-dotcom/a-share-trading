@echo off
chcp 65001 >nul
cd /d "%~dp0"
title A股三核研究系统

echo.
echo ╔═══════════════════════════════════════╗
echo ║    A股三核研究系统                     ║
echo ║                                       ║
echo ║  推送 / 基本面 / 技术面               ║
echo ║  浏览器打开 http://localhost:5000      ║
echo ║  Ctrl+C 停止服务                      ║
echo ╚═══════════════════════════════════════╝
echo.

python server.py
pause
