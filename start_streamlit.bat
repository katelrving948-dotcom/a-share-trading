@echo off
chcp 65001 >nul
title A股短线交易系统 - Streamlit仪表盘

echo.
echo ╔═══════════════════════════════════════╗
echo ║    📊 A股短线交易系统 · Streamlit     ║
echo ║                                       ║
echo ║  启动中，请稍候...                     ║
echo ║  浏览器打开后访问 http://localhost:8501 ║
echo ╚═══════════════════════════════════════╝
echo.

pip install streamlit plotly -q 2>nul

streamlit run app_streamlit.py

pause
