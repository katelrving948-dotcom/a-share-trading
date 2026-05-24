@echo off
chcp 65001 >nul
title A股短线交易系统 - Web仪表盘

echo.
echo ╔═══════════════════════════════════════╗
echo ║    📊 A股短线交易系统 · Web仪表盘      ║
echo ║                                       ║
echo ║  启动中，请稍候...                     ║
echo ║  浏览器打开后访问 http://localhost:5000 ║
echo ╚═══════════════════════════════════════╝
echo.

pip install flask flask-cors -q 2>nul

python app.py

pause
