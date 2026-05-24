#!/usr/bin/env python3
"""
A股短线交易系统 - Flask Web 可视化仪表盘
=========================================
启动: python app.py
访问: http://localhost:5000
"""

import os
import sys
import json
import traceback
from datetime import datetime, timedelta
from threading import Thread
from functools import wraps

import pandas as pd
from flask import Flask, jsonify, render_template, request, send_file

from config import SCREEN, INDICATORS, STRATEGY, RISK, OUTPUT
from data_feed import DataFeed
from screener import StockScreener
from strategy import StrategyEngine, SignalType
from monitor import TradeManager, Monitor, Position
from backtest import BacktestEngine
from analysis import Analyzer

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

df = DataFeed()
strategy_engine = StrategyEngine()
analyzer = Analyzer()


# ─── 首页 ───

@app.route("/")
def index():
    return render_template("index.html")


# ─── API: 全市场概览 ───

@app.route("/api/market-overview")
def api_market_overview():
    """市场概览数据"""
    try:
        stocks = df.get_stock_list()
        if stocks.empty:
            return jsonify({"error": "获取数据失败"})

        stats = {
            "total": len(stocks),
            "up": int((stocks["change_pct"] > 0).sum()),
            "down": int((stocks["change_pct"] < 0).sum()),
            "flat": int((stocks["change_pct"] == 0).sum()),
            "limit_up": int((stocks["change_pct"] >= 9.8).sum()),
            "limit_down": int((stocks["change_pct"] <= -9.8).sum()),
            "avg_change": round(stocks["change_pct"].mean(), 2),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        # 涨幅榜前10
        top_gainers = stocks.nlargest(10, "change_pct")[
            ["code", "name", "price", "change_pct", "turnover_rate", "market_cap"]
        ].to_dict("records")
        # 跌幅榜前10
        top_losers = stocks.nsmallest(10, "change_pct")[
            ["code", "name", "price", "change_pct", "turnover_rate", "market_cap"]
        ].to_dict("records")

        return jsonify({"stats": stats, "gainers": top_gainers, "losers": top_losers})
    except Exception as e:
        return jsonify({"error": str(e), "traceback": traceback.format_exc()})


# ─── API: 选股 ───

@app.route("/api/screen")
def api_screen():
    """执行选股并返回结果"""
    try:
        screener = StockScreener()
        result = screener.screen()
        if result.empty:
            return jsonify({"stocks": [], "count": 0, "message": "无符合条件标的"})

        stocks = result.to_dict("records")
        return jsonify({
            "stocks": stocks,
            "count": len(stocks),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
    except Exception as e:
        return jsonify({"error": str(e), "traceback": traceback.format_exc()})


# ─── API: 股票K线 + 技术指标 ───

@app.route("/api/kline/<code>")
def api_kline(code: str):
    """获取股票K线和指标"""
    try:
        period = request.args.get("period", "day")
        count = int(request.args.get("count", 120))

        kline = df.get_kline(code, period=period, count=count)
        if kline.empty:
            return jsonify({"error": "获取K线失败"})

        # 获取股票基本信息
        stocks = df.get_stock_list()
        stock_info = {}
        if not stocks.empty:
            match = stocks[stocks["code"] == code]
            if not match.empty:
                s = match.iloc[0]
                stock_info = {
                    "code": code,
                    "name": s.get("name", ""),
                    "price": s.get("price", 0),
                    "change_pct": s.get("change_pct", 0),
                    "market_cap": s.get("market_cap", 0),
                    "turnover_rate": s.get("turnover_rate", 0),
                    "pe": s.get("pe", 0),
                }

        # 转换K线数据为前端格式
        kline_data = []
        for _, row in kline.iterrows():
            item = {
                "date": row["date"].strftime("%Y-%m-%d") if hasattr(row["date"], "strftime") else str(row["date"]),
                "open": round(float(row["open"]), 2),
                "close": round(float(row["close"]), 2),
                "high": round(float(row["high"]), 2),
                "low": round(float(row["low"]), 2),
                "volume": float(row["volume"]),
            }
            for col in ["MA5", "MA10", "MA20", "MA60",
                        "MACD_DIF", "MACD_DEA", "MACD_BAR",
                        "RSI", "KDJ_K", "KDJ_D", "KDJ_J",
                        "BOLL_UP", "BOLL_MID", "BOLL_DN",
                        "VOL_MA5", "VOL_MA10"]:
                if col in kline.columns and not pd.isna(row.get(col)):
                    item[col] = round(float(row[col]), 2)
            kline_data.append(item)

        # 生成买卖信号
        signal = strategy_engine.generate_buy_signals(
            kline, {"code": code, "name": stock_info.get("name", ""), "price": stock_info.get("price", 0)}
        )

        return jsonify({
            "stock": stock_info,
            "kline": kline_data,
            "signal": {
                "has_signal": signal is not None,
                "type": signal.signal.value if signal else "",
                "reason": signal.reason if signal else "",
                "score": signal.score if signal else 0,
                "stop_loss": signal.stop_loss if signal else 0,
                "take_profit": signal.take_profit if signal else 0,
            } if signal else {"has_signal": False},
        })
    except Exception as e:
        return jsonify({"error": str(e), "traceback": traceback.format_exc()})


# ─── API: 回测 ───

@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    """执行回测"""
    try:
        data = request.get_json() or {}
        codes = data.get("codes", [])
        days = int(data.get("days", 120))

        if not codes:
            return jsonify({"error": "请提供股票代码"})

        bt = BacktestEngine()
        results = bt.run_multi(codes, days=days)

        if results.empty:
            return jsonify({"results": [], "message": "回测无结果"})

        return jsonify({
            "results": results.to_dict("records"),
            "count": len(results),
        })
    except Exception as e:
        return jsonify({"error": str(e), "traceback": traceback.format_exc()})


@app.route("/api/backtest/<code>")
def api_backtest_single(code: str):
    """回测单只股票"""
    try:
        days = int(request.args.get("days", 120))
        bt = BacktestEngine()
        result = bt.run(code, "", days=days)

        if not result:
            return jsonify({"error": "回测失败"})

        return jsonify({
            "code": result["code"],
            "name": result.get("name", ""),
            "total_return": result["total_return"],
            "annual_return": result["annual_return"],
            "max_drawdown": result["max_drawdown"],
            "win_rate": result["win_rate"],
            "sharpe_ratio": result["sharpe_ratio"],
            "trade_count": result["trade_count"],
            "profit_loss_ratio": result["profit_loss_ratio"],
            "trades": result.get("trades", []),
            "equity_curve": result.get("equity_curve", []),
        })
    except Exception as e:
        return jsonify({"error": str(e), "traceback": traceback.format_exc()})


# ─── API: 实时行情 ───

@app.route("/api/realtime")
def api_realtime():
    """获取实时行情"""
    try:
        codes_str = request.args.get("codes", "")
        if not codes_str:
            return jsonify({"error": "请提供股票代码"})
        codes = [c.strip() for c in codes_str.split(",")]

        quotes = df.get_realtime_quotes(codes)
        if quotes.empty:
            return jsonify({"error": "获取行情失败", "quotes": []})

        return jsonify({"quotes": quotes.to_dict("records")})
    except Exception as e:
        return jsonify({"error": str(e)})


# ─── API: 搜索股票 ───

@app.route("/api/search")
def api_search():
    """搜索股票"""
    try:
        keyword = request.args.get("q", "").strip().upper()
        if not keyword:
            return jsonify({"stocks": []})

        stocks = df.get_stock_list()
        if stocks.empty:
            return jsonify({"stocks": []})

        result = stocks[
            stocks["code"].str.contains(keyword) |
            stocks["name"].str.contains(keyword, na=False)
        ].head(20)

        return jsonify({
            "stocks": result[["code", "name", "price", "change_pct", "board"]].to_dict("records")
        })
    except Exception as e:
        return jsonify({"error": str(e)})


# ─── 启动 ───

def print_startup_info():
    print("""
╔═══════════════════════════════════════════╗
║      📊 A股短线交易系统 · Web仪表盘       ║
║                                           ║
║  访问地址: http://localhost:5000           ║
║                                           ║
║  盘前选股 | 盘中监控 | 回测分析 | 复盘     ║
╚═══════════════════════════════════════════╝
    """)


if __name__ == "__main__":
    import webbrowser
    from threading import Timer

    def open_browser():
        webbrowser.open("http://localhost:5000")

    print_startup_info()
    Timer(1.5, open_browser).start()
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
