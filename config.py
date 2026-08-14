"""
A股中长期研究与交易系统 - 配置文件
=========================
所有可调参数集中管理，便于策略调整。
"""
import os

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

# ========== 兼容技术筛选接口的评分权重（不属于回测交易方案） ==========
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

# ========== 中长期选股（目标持仓至少一周） ==========
LONG_TERM = {
    "holding_horizon": "至少1周，重点观察1至6个月",
    # 0 表示对通过初步交易性过滤的全部股票拉取财务数据，不做人为截断。
    "universe_limit": 0,
    "result_limit": 50,
    "recommendation_count": 10,
    "minimum_score": 50,
    "market_cap_min": 50,
    "average_amount_min": 0.2,
    "turnover_max": 12.0,
    "weights": {
        "quality": 0.35,
        "growth": 0.30,
        "valuation": 0.20,
        "cashflow": 0.15,
    },
    "composite_weights": {
        "fundamental": 0.70,
        "technical": 0.30,
    },
    "selection_weights": {
        "composite": 0.80,
        "market_flow": 0.20,
    },
    # 外盘结构分在有真实快照时参与规则轮动；AI只在返回可解析结果时受控叠加。
    "rotation_external_weight": 0.20,
    "rotation_ai_weight": 0.25,
    "theme_board_limit": 8,
    # 热门核心票独立于基本面硬门槛：从强势板块中各取龙头、次龙头。
    "hot_core_board_limit": 5,
    "hot_core_count": 10,
    "hot_core_minimum_board_score": 55,
}

# ========== 选股推送决策门槛 ==========
# 排名只负责形成观察池；板块、个股和实时入场三道门槛全部通过后，
# 才允许在邮件和网页中显示“可执行观察”。金额均为风险预算参考，不是自动委托。
PUSH_DISCIPLINE = {
    "reference_capital": 50000,
    "risk_per_trade_pct": 0.005,
    "max_single_position_pct": 0.20,
    "max_daily_loss_pct": 0.01,
    "max_weekly_loss_pct": 0.02,
    "max_new_trades_per_day": 1,
    "board_min_score": 60,
    "stock_min_score": 60,
    "allow_profit_add": False,
}

# ========== 风险管理 ==========
RISK = {
    "position_pct": 0.2,
    "max_exposure": 0.8,
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

# ========== 尾盘潜伏选股策略 ==========
TAIL_END = {
    "holding_horizon": "尾盘买入，目标持仓1-3日",
    # Stage 1: 昨日初筛
    "stage1_pool_size": 200,
    "stage1_max_candidates": 1000,
    "stage1_preferred_market_cap": 180,
    "market_cap_min": 30,
    "price_min": 5.0,
    "price_max": 100.0,
    "exclude_st": True,
    "exclude_bj": True,
    "yesterday_change_min": -5.0,
    "yesterday_change_max": 5.0,
    "volume_ratio_min": 0.8,
    "rsi_min": 35,
    "rsi_max": 65,
    "stage1_kline_count": 80,
    "stage1_weights": {
        "volume": 0.25,
        "ma_structure": 0.25,
        "macd": 0.20,
        "price_position": 0.15,
        "rsi": 0.15,
    },
    # Stage 2: 今日盘中验证
    "today_change_min": 0.0,
    "today_change_max": 5.0,
    "today_volume_ratio_min": 0.8,
    "today_volume_ratio_max": 2.5,
    "today_main_net_min": 0,
    "today_main_net_pct_min": 0.0,
    "today_main_net_pct_max": 10.0,
    "reject_tail_down": True,
    "require_sector_match": True,
    "rotation_board_limit": 10,
    "stage2_candidate_limit": 50,
    "stage2_weights": {
        "volume_price": 0.30,
        "fund_flow": 0.30,
        "intraday_trend": 0.25,
        "sector_flow": 0.15,
    },
    # Stage 3: 尾盘精选
    "composite_weights": {
        "stage1": 0.30,
        "stage2": 0.70,
    },
    "recommendation_count": 10,
    "max_per_industry": 2,
    "max_per_concept": 2,
}

# ========== AI 选股助手配置 ==========
# DeepSeek API: https://platform.deepseek.com/
AI_CONFIG = {
    "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
    "api_url": "https://api.deepseek.com/v1/chat/completions",
    "model": "deepseek-chat",
    "max_tokens": 4096,
    "temperature": 0.7,
    "system_prompt": (
        "你是量化研究员。请基于目标A股行业板块（如创新药、科技、半导体等）的最新可得财报数据，"
        "优化该行业的选股参数，包括毛利率、ROE、PE区间和估值系数，并给出合理取值区间及估算依据。"
        "应优先采用行业内可比公司的分位数、中位数和离散程度进行校准，按行业分别设定阈值或标准化评分，"
        "避免行业固有差异导致评分偏差过大、选股失准。只能依据调用时提供的财务API数据、技术评分、"
        "板块资金流、行情与新闻分析；不得虚构财报、公告或预测。缺少目标行业、报告期、样本量或关键指标时，"
        "必须明确说明数据不足，不得给出伪精确结论。输出需说明目标行业、数据报告期、样本口径、各参数建议区间、"
        "估值系数、估算依据及风险。"
    ),
    "rotation_system_prompt": (
        "你是A股板块轮动研究员。请依据调用时提供的每日市场宽度、行业与概念板块资金强度、"
        "可核验的美股/中概股/港股/大宗商品快照、国内政策和带时间来源的新闻事件，研判资金可能从哪些板块流出并切换到哪些板块，"
        "识别市场及板块处于延续、分歧、退潮、超跌反弹还是趋势反转阶段。"
        "必须区分已提供的客观数据、基于数据的推断和仍待验证的条件；不得虚构政策、新闻、"
        "外盘行情、历史连续资金数据或确定性收益。单日上涨、单日流入或单条消息只能作为反弹/轮动线索，不能单独认定反转。"
        "结论需给出时间窗口、信号强弱、触发条件、失效条件和主要风险。"
    ),
    "enable_news": True,
    "news_count": 30,
}
