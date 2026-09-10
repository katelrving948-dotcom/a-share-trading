"""Configuration used by the three-core research system."""

REQUEST_TIMEOUT = 15
REQUEST_RETRIES = 3
REQUEST_INTERVAL = 0.3

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

LONG_TERM = {
    "universe_limit": 0,
    "minimum_score": 60,
    "market_cap_min": 20,  # 亿元；扩大到中小市值，基本面门槛不降
    "small_cap_boundary": 500,
    "small_cap_amount_min": 0.5,  # 亿元；使用行情快照成交额，不冒称日均
    "average_amount_min": 0.2,
    "turnover_max": 12.0,
    "weights": {
        "quality": 0.35,
        "growth": 0.30,
        "valuation": 0.20,
        "cashflow": 0.15,
    },
}

PULLBACK = {
    "version": 1,
    "support_atr": 0.5,
    "minimum_retrace_atr": 1.0,
    "max_entry_atr": 0.5,
    "stop_buffer_atr": 0.2,
    "valid_sessions": 5,
    "minimum_risk_pct": 0.04,
    "maximum_risk_pct": 0.07,
    "minimum_reward_risk": 1.5,
}
