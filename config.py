"""
A股短线交易系统 - 配置文件
=========================
所有可调参数集中管理，便于策略调整。
"""

# ========== 数据源配置 ==========
REQUEST_TIMEOUT = 15
REQUEST_RETRIES = 3
REQUEST_INTERVAL = 0.3

# ========== 选股过滤条件 ==========
SCREEN = {
    "market_cap_min": 30,
    "market_cap_max": 2000,
    "price_min": 3.0,
    "price_max": 200.0,
    "avg_amount_min": 0.5,
    "turnover_min": 1.0,
    "turnover_max": 20.0,
    "exclude_st": True,
    "exclude_kcb": False,
    "exclude_bj": True,
}

# ========== 技术指标参数 ==========
INDICATORS = {
    "ma_short": 5,
    "ma_medium": 10,
    "ma_long": 20,
    "ma_trend": 60,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "rsi_period": 14,
    "rsi_overbought": 75,
    "rsi_oversold": 25,
    "kdj_k": 9,
    "kdj_d": 3,
    "volume_ratio": 1.5,
}

# ========== 策略权重 ==========
STRATEGY = {
    "strategy_weights": {
        "volume_breakout": 0.25,
        "ma_golden_cross": 0.20,
        "macd_signal": 0.20,
        "kdj_signal": 0.15,
        "volume_price": 0.20,
    },
    "score_threshold": 60,
    "max_stocks": 15,
}

# ========== 风险管理 ==========
RISK = {
    "position_pct": 0.2,
    "stop_loss": -0.03,
    "take_profit": 0.06,
    "max_positions": 5,
    "max_trades_per_day": 3,
    "max_drawdown": -0.10,
}

# ========== 交易时段 ==========
TRADING_HOURS = {
    "morning_start": "09:30",
    "morning_end": "11:30",
    "afternoon_start": "13:00",
    "afternoon_end": "15:00",
}

# ========== 回测配置 ==========
BACKTEST = {
    "initial_capital": 100000,
    "commission": 0.00025,
    "stamp_tax": 0.001,
    "slippage": 0.001,
}

# ========== 输出配置 ==========
OUTPUT = {
    "screen_result_file": "选股结果.csv",
    "trade_log_file": "交易记录.csv",
    "analysis_report_file": "复盘报告.html",
    "chart_dir": "charts",
}
