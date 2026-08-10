"""
中长期综合选股器。

先用流动性与交易边界建立可执行股票池，并为池内全部股票请求最新财务
指标报告；基本面合格后追加技术评分，再结合近期板块资金热点精选十股。
"""
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from config import LONG_TERM, SCREEN
from data_feed import DataFeed


def _clip(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _growth_score(value: float) -> float:
    if value <= -20:
        return 0
    if value < 0:
        return 25
    if value < 5:
        return 50
    if value < 15:
        return 70
    if value < 35:
        return 90
    return 80


def _build_rule_rotation_analysis(context: dict, boards: list) -> dict:
    """在AI不可用时，仍用市场宽度、资金和外部因子形成条件化研判。"""
    stats = context.get("market_stats", {})
    up = int(stats.get("up") or 0)
    down = int(stats.get("down") or 0)
    avg_change = float(stats.get("avg_change") or 0)
    advance_ratio = up / max(down, 1)
    if advance_ratio >= 1.3 and avg_change > 0.5:
        stage = "趋势延续/扩散"
    elif advance_ratio <= 0.75 and avg_change < -0.5:
        stage = "退潮/防守"
    else:
        stage = "震荡分歧"

    external = context.get("external_market") or {}
    markets = external.get("markets", [])
    positive = sorted(markets, key=lambda item: item.get("change_pct", 0), reverse=True)
    negative = sorted(markets, key=lambda item: item.get("change_pct", 0))
    external_summary = "外盘快照不可用，未把外部方向计入规则分。"
    if external.get("available"):
        lead = "、".join(
            f"{item.get('name')}{float(item.get('change_pct') or 0):+.2f}%"
            for item in positive[:2]
        )
        weak = "、".join(
            f"{item.get('name')}{float(item.get('change_pct') or 0):+.2f}%"
            for item in negative[:2]
        )
        external_summary = f"外盘强项：{lead}；弱项：{weak}。"

    externally_supported = [
        board for board in boards
        if board.get("external_signal_count") and board.get("external_score", 50) >= 58
    ]
    externally_pressured = [
        board for board in boards
        if board.get("external_signal_count") and board.get("external_score", 50) <= 42
    ]
    supported_names = "、".join(board.get("name", "") for board in externally_supported[:3])
    pressured_names = "、".join(board.get("name", "") for board in externally_pressured[:3])
    top_funded = "、".join(board.get("name", "") for board in boards[:3]) or "暂无"
    short = f"1-3日优先观察资金前列的{top_funded}"
    if supported_names:
        short += f"；其中{supported_names}同时获得外盘/事件方向支持"
    if pressured_names:
        short += f"。{pressured_names}存在外部逆风，需等资金继续增强再确认"
    short += "。"
    medium = "3-10日只在板块连续流入、上涨家数扩散且外部催化未反转时延续判断；否则按轮动线索而非趋势确认处理。"
    return {
        "available": False,
        "external_available": bool(external.get("available")),
        "mode": "rule_external" if external.get("available") else "rule_only",
        "market_stage": stage,
        "market_stage_reason": (
            f"上涨{up}家、下跌{down}家、平均涨跌{avg_change:+.2f}%；"
            f"板块排序综合当日/近5日资金与外部影响。"
        ),
        "short_term_outlook": short,
        "medium_term_outlook": medium,
        "external_driver_summary": external_summary,
        "external_market": external,
        "rotation_path": [],
        "boards": [],
        "risks": list(external.get("limitations", [])),
        "reason": "未配置AI或AI不可用，使用资金+外盘+事件规则评分。",
    }


def build_opening_entry_plan(intraday: dict) -> dict:
    """Turn the completed 09:30-10:00 window into a bounded execution plan."""
    window = (intraday or {}).get("opening_30m") or {}
    base = {
        "window": "09:30-10:00",
        "actionable": False,
        "status": window.get("status") or "首30分钟数据不可用",
        "entry_zone": None,
        "breakout_trigger": None,
        "max_chase_price": None,
        "stop_zone": None,
        "take_profit_zones": [],
        "risk_pct": None,
        "reference_price": None,
        "execution_state": "等待首30分钟完成",
        "levels_available": False,
        "execution_note": (
            "普通A股当日买入通常不能当日卖出；止损止盈为条件计划，"
            "跳空或流动性不足可能导致实际成交偏离。"
        ),
        "reason": "等待首30分钟形成完整价格区间。",
        "opening": window,
    }
    if not window.get("completed"):
        return base

    try:
        open_price = float(window["open"])
        high = float(window["high"])
        low = float(window["low"])
        close = float(window["close"])
        vwap = float(window["vwap"])
        change_pct = float(window.get("change_pct") or 0)
        range_pct = float(window.get("range_pct") or 0)
        up_ratio = float(window.get("up_minute_ratio") or 0)
        close_position = float(window.get("close_position") or 0)
        above_vwap_ratio = float(window.get("above_vwap_ratio") or 0)
    except (KeyError, TypeError, ValueError):
        base.update(status="首30分钟数据不完整", reason="缺少开盘区间或分时均价。")
        return base

    if min(open_price, high, low, close, vwap) <= 0 or high < low:
        base.update(status="首30分钟数据异常", reason="价格字段无效，不能计算进场计划。")
        return base

    if range_pct > 4.5 or change_pct > 3.5:
        base.update(
            status="暂不追涨",
            reason=(
                f"首30分钟涨幅{change_pct:+.2f}%、振幅{range_pct:.2f}%，"
                "波动或涨幅过大；等待回落重新形成支撑。"
            ),
        )
        return base

    if (close < vwap * 0.995 or close_position < 0.35
            or above_vwap_ratio < 0.40 or change_pct < -1.5):
        base.update(
            status="暂不进场",
            reason=(
                f"10:00价格相对区间位置{close_position:.0%}，"
                f"位于分时均价上方的时间占比{above_vwap_ratio:.0%}；"
                "首30分钟承接不足。"
            ),
        )
        return base

    strong = (
        close >= vwap
        and close_position >= 0.65
        and up_ratio >= 0.50
        and above_vwap_ratio >= 0.55
    )
    entry_low_factor = 0.995 if strong else 0.990
    entry_high_factor = 1.005 if strong else 1.002
    entry_low = max(low, vwap * entry_low_factor)
    entry_high = min(high, vwap * entry_high_factor)
    if entry_high < entry_low:
        entry_high = min(high, entry_low * 1.005)
    if entry_high < entry_low:
        base.update(status="暂不进场", reason="分时均价与开盘区间无法形成有效回踩区间。")
        return base

    entry_mid = (entry_low + entry_high) / 2
    risk_fraction = min(0.05, max(0.02, range_pct / 100 * 0.8))
    stop_reference = max(low * 0.995, entry_low * (1 - risk_fraction))
    stop_low = stop_reference * 0.995
    stop_high = min(stop_reference * 1.002, entry_low * 0.995)
    actual_risk = max(0.0, (entry_mid - stop_high) / entry_mid * 100)
    risk_per_share = max(entry_mid - stop_high, entry_mid * 0.005)
    first_target_low = max(high * 1.005, entry_mid + risk_per_share * 1.5)
    first_target_high = first_target_low * 1.008
    second_target_low = max(first_target_high + 0.01, entry_mid + risk_per_share * 2.5)
    second_target_high = second_target_low * 1.012
    max_chase_price = high * 1.015
    current_price = float((intraday or {}).get("close_price") or close)
    execution_state = "等待回踩进场区或放量突破确认"
    actionable = True
    status = "强势回踩" if strong else "均价承接确认"
    if current_price < stop_high:
        actionable = False
        status = "首30分钟结构已失效"
        execution_state = "当前价已跌入止损区下方，不按原计划进场"
    elif current_price > max_chase_price:
        actionable = False
        status = "当前价格暂不追涨"
        execution_state = "当前价已超过禁止追价上限，等待重新形成支撑"
    elif entry_low <= current_price <= entry_high:
        execution_state = "当前价进入回踩进场区，可结合量能分批观察"
    elif current_price < entry_low:
        execution_state = "当前价低于计划进场区，等待重新站回分时均价确认"
    elif current_price >= high * 1.002:
        execution_state = "已触发突破确认，但未超过禁追线，避免一次性追入"
    base.update({
        "actionable": actionable,
        "levels_available": True,
        "status": status,
        "entry_zone": {
            "low": round(entry_low, 2),
            "high": round(entry_high, 2),
            "label": "回踩分时均价附近分批观察",
        },
        "breakout_trigger": round(high * 1.002, 2),
        "max_chase_price": round(max_chase_price, 2),
        "stop_zone": {
            "low": round(stop_low, 2),
            "high": round(stop_high, 2),
            "label": "跌入区间视为首30分钟结构失效",
        },
        "take_profit_zones": [
            {
                "name": "第一止盈",
                "low": round(first_target_low, 2),
                "high": round(first_target_high, 2),
                "risk_reward": round(
                    (first_target_low - entry_mid) / risk_per_share, 2
                ),
                "action": "到达后可减仓约三分之一，并将保护位上移至进场均价附近",
            },
            {
                "name": "第二止盈",
                "low": round(second_target_low, 2),
                "high": round(second_target_high, 2),
                "risk_reward": round(
                    (second_target_low - entry_mid) / risk_per_share, 2
                ),
                "action": "到达后可继续减仓，剩余仓位按分时均价或短期均线跟踪",
            },
        ],
        "risk_pct": round(actual_risk, 2),
        "reference_price": round(current_price, 2),
        "execution_state": execution_state,
        "reason": (
            f"首30分钟收盘{close:.2f}，分时均价{vwap:.2f}，"
            f"收在区间{close_position:.0%}位置，上涨分钟占比{up_ratio:.0%}。"
        ),
    })
    return base


class LongTermFundamentalScreener:
    """面向一周以上持仓周期的可复核综合选股流程。"""

    def __init__(self, data_feed=None):
        self.df = data_feed if data_feed is not None else DataFeed()
        self._progress = {"state": "idle", "done": 0, "total": 0}
        self.summary = {}
        self.recommendations = []

    @property
    def progress(self) -> dict:
        return dict(self._progress)

    def screen(self, universe_limit: int = None, ai_advisor=None) -> list:
        stocks = self.df.get_stock_list()
        if stocks.empty:
            self.summary = {"error": "获取市场股票池失败"}
            self.recommendations = []
            return []

        pool = self._candidate_pool(stocks)
        # 默认覆盖全部初筛股票；正数限制仅保留给命令行诊断/兼容调用。
        limit = LONG_TERM["universe_limit"] if universe_limit is None else int(universe_limit)
        if limit > 0:
            pool = pool.head(limit)

        codes = pool["code"].tolist()
        self._progress = {
            "state": "fundamentals",
            "done": 0,
            "total": len(codes),
            "message": "逐股拉取最新财务指标报告",
        }

        def on_progress(done, total, code, available):
            self._progress.update({
                "state": "fundamentals",
                "done": done,
                "total": total,
                "current_code": str(code),
                "financial_available": available,
            })

        financials = self.df.get_financials_batch(codes, progress_callback=on_progress)
        self._progress.update({"state": "scoring", "message": "计算中长期基本面评分"})

        ranked = []
        successful = 0
        for _, quote in pool.iterrows():
            financial = financials.get(str(quote["code"]), {})
            if not financial.get("available"):
                continue
            successful += 1
            ranked.append(self._evaluate(financial))

        fundamental_ranked = [
            item for item in ranked
            if item["fundamental_score"] >= LONG_TERM["minimum_score"]
        ]
        fundamental_ranked.sort(key=lambda item: item["fundamental_score"], reverse=True)

        self._progress.update({
            "state": "technical",
            "done": 0,
            "total": len(fundamental_ranked),
            "message": "在基本面合格池上计算技术评分与综合排名",
        })
        with ThreadPoolExecutor(max_workers=min(6, len(fundamental_ranked) or 1)) as executor:
            futures = {
                executor.submit(self._technical_analysis, item["code"]): item
                for item in fundamental_ranked
            }
            for index, future in enumerate(as_completed(futures), start=1):
                item = futures[future]
                try:
                    item.update(future.result())
                except Exception:
                    item.update({
                        "technical_available": False,
                        "technical_score": 0,
                        "technical_reason": "技术数据请求失败",
                        "trend_confirmation": "趋势数据不可用",
                    })
                item["composite_score"] = self._composite_score(item)
                self._progress.update({
                    "done": index,
                    "current_code": item["code"],
                    "technical_available": item.get("technical_available", False),
                })

        fundamental_ranked.sort(key=lambda item: item["composite_score"], reverse=True)
        self._progress.update({
            "state": "market",
            "done": len(fundamental_ranked),
            "total": len(fundamental_ranked),
            "message": "结合板块资金、外盘联动与事件冲击精选十股",
        })
        selected, rotation_boards, rotation_analysis = self._select_recommendations(
            fundamental_ranked, ai_advisor=ai_advisor
        )
        self.recommendations = selected[:LONG_TERM["recommendation_count"]]
        self._attach_opening_plans(self.recommendations)
        recommendation_codes = {
            item["code"]: rank for rank, item in enumerate(self.recommendations, start=1)
        }
        for item in fundamental_ranked:
            item["recommendation_rank"] = recommendation_codes.get(item["code"])

        self.summary = {
            "holding_horizon": LONG_TERM["holding_horizon"],
            "pool_count": len(pool),
            "financial_success_count": successful,
            "fundamental_qualified_count": len(fundamental_ranked),
            "selected_count": len(self.recommendations),
            "entry_plan_ready_count": sum(
                1 for item in self.recommendations
                if (item.get("opening_plan") or {}).get("actionable")
            ),
            "comprehensive_count": min(len(fundamental_ranked), LONG_TERM["result_limit"]),
            "scan_scope": "全部初筛股票" if not limit else f"诊断限制 {limit} 只",
            "data_source": "东方财富财务指标 API / K线指标 / 板块资金流 / 外盘与商品快照 / 财经快讯事件",
            "weights": dict(LONG_TERM["weights"]),
            "composite_weights": dict(LONG_TERM["composite_weights"]),
            "selection_weights": dict(LONG_TERM["selection_weights"]),
            "rotation_external_weight": LONG_TERM.get("rotation_external_weight", 0),
            "rotation_ai_weight": LONG_TERM.get("rotation_ai_weight", 0),
            "rotation_boards": rotation_boards[:8],
            "rotation_analysis": rotation_analysis,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._progress.update({"state": "done", "done": len(codes), "total": len(codes)})
        return fundamental_ranked[:LONG_TERM["result_limit"]]

    def _attach_opening_plans(self, candidates: list) -> None:
        if not candidates:
            return
        self._progress.update({
            "state": "opening_plan",
            "done": 0,
            "total": len(candidates),
            "message": "根据09:30-10:00分时生成进场与止损区间",
        })
        with ThreadPoolExecutor(max_workers=min(6, len(candidates))) as executor:
            futures = {
                executor.submit(self.df.get_intraday_minute, item["code"]): item
                for item in candidates
            }
            for index, future in enumerate(as_completed(futures), start=1):
                item = futures[future]
                try:
                    item["opening_plan"] = build_opening_entry_plan(future.result())
                except Exception as exc:
                    item["opening_plan"] = {
                        "window": "09:30-10:00",
                        "actionable": False,
                        "status": "首30分钟计划失败",
                        "reason": str(exc),
                        "entry_zone": None,
                        "stop_zone": None,
                    }
                self._progress.update({
                    "done": index,
                    "current_code": item["code"],
                })

    def _candidate_pool(self, stocks: pd.DataFrame) -> pd.DataFrame:
        """过滤不可执行标的；排序只控制请求批次，不参与财务评分。"""
        conditions = (
            (stocks["price"] >= SCREEN["price_min"]) &
            (stocks["price"] <= SCREEN["price_max"]) &
            (stocks["market_cap"] >= LONG_TERM["market_cap_min"]) &
            (stocks["amount"] >= LONG_TERM["average_amount_min"] * 1e8) &
            (stocks["turnover_rate"] <= LONG_TERM["turnover_max"])
        )
        if SCREEN["exclude_st"]:
            conditions &= ~stocks["is_st"]
        if SCREEN["exclude_kcb"]:
            conditions &= stocks["board"] != "科创板"
        if SCREEN["exclude_bj"]:
            conditions &= stocks["board"] != "北交所"
        return stocks[conditions].copy().sort_values("amount", ascending=False)

    def _evaluate(self, financial: dict) -> dict:
        roe = float(financial.get("annualized_roe") or 0)
        revenue_growth = float(financial.get("revenue_growth") or 0)
        profit_growth = float(financial.get("profit_growth") or 0)
        gross_margin = financial.get("gross_margin")
        eps = float(financial.get("eps") or 0)
        cash_ps = float(financial.get("operating_cf_per_share") or 0)
        pe = float(financial.get("pe") or 0)
        pb = float(financial.get("pb") or 0)

        roe_score = _clip(roe * 5)
        margin_score = _clip(float(gross_margin) * 2) if gross_margin is not None else roe_score
        quality = round(roe_score * 0.7 + margin_score * 0.3)

        growth = round(_growth_score(revenue_growth) * 0.4 + _growth_score(profit_growth) * 0.6)

        if pe <= 0:
            pe_score = 10
        elif pe <= 12:
            pe_score = 90
        elif pe <= 25:
            pe_score = 78
        elif pe <= 40:
            pe_score = 52
        elif pe <= 70:
            pe_score = 28
        else:
            pe_score = 10
        if pb <= 0:
            pb_score = 35
        elif pb <= 2:
            pb_score = 90
        elif pb <= 5:
            pb_score = 65
        elif pb <= 10:
            pb_score = 40
        else:
            pb_score = 18
        valuation = round(pe_score * 0.65 + pb_score * 0.35)

        if eps <= 0:
            cashflow = 10 if cash_ps >= 0 else 0
        else:
            coverage = cash_ps / eps
            cashflow = round(_clip(coverage * 70))

        weights = LONG_TERM["weights"]
        score = round(
            quality * weights["quality"] +
            growth * weights["growth"] +
            valuation * weights["valuation"] +
            cashflow * weights["cashflow"]
        )
        reasons = [
            f"{financial.get('report_date', '--')} 报告期",
            f"ROE年化参考 {roe:.1f}%",
            f"营收/净利同比 {revenue_growth:+.1f}%/{profit_growth:+.1f}%",
        ]
        risks = []
        if eps <= 0:
            risks.append("每股收益非正")
        if revenue_growth < 0 or profit_growth < 0:
            risks.append("盈利增长承压")
        if pe <= 0 or pe > 50:
            risks.append("估值指标异常或偏高")
        if eps > 0 and cash_ps < eps * 0.5:
            risks.append("经营现金流覆盖偏低")
        if gross_margin is None:
            risks.append("毛利率字段不适用于或未披露")

        return {
            "code": financial["code"],
            "name": financial.get("name", ""),
            "board": financial.get("board", ""),
            "price": financial.get("price", 0),
            "market_cap": financial.get("market_cap", 0),
            "report_date": financial.get("report_date", ""),
            "notice_date": financial.get("notice_date", ""),
            "fundamental_score": score,
            "quality_score": quality,
            "growth_score": growth,
            "valuation_score": valuation,
            "cashflow_score": cashflow,
            "roe": financial.get("roe", 0),
            "annualized_roe": financial.get("annualized_roe", 0),
            "revenue_growth": revenue_growth,
            "profit_growth": profit_growth,
            "gross_margin": gross_margin,
            "eps": eps,
            "operating_cf_per_share": cash_ps,
            "pe": pe,
            "pb": pb,
            "main_net": financial.get("main_net", 0),
            "main_net_pct": financial.get("main_net_pct", 0),
            "fundamental_reason": "；".join(reasons),
            "risk": "；".join(risks) if risks else "未触发量化财务警示，仍需核验公告",
            "data_source": financial.get("data_source", ""),
        }

    def _technical_analysis(self, code: str) -> dict:
        kline = self.df.get_kline(code, count=80)
        if kline.empty:
            return {
                "technical_available": False,
                "technical_score": 0,
                "technical_reason": "技术数据不可用",
                "trend_confirmation": "趋势数据不可用",
            }
        latest = kline.iloc[-1]
        previous = kline.iloc[-2] if len(kline) > 1 else latest
        close = float(latest.get("close") or 0)
        ma20 = latest.get("MA20")
        ma60 = latest.get("MA60")
        score = 0
        reasons = []
        if pd.notna(ma20) and pd.notna(ma60) and close > ma20 > ma60:
            trend = "站上MA20/MA60，中期趋势确认"
            score += 35
            reasons.append("中期均线多头")
        elif pd.notna(ma20) and close > ma20:
            trend = "站上MA20，等待长期趋势确认"
            score += 20
            reasons.append("站上MA20")
        else:
            trend = "未站上MA20，仅保留基本面观察"

        dif = latest.get("MACD_DIF")
        dea = latest.get("MACD_DEA")
        if pd.notna(dif) and pd.notna(dea) and dif > dea:
            score += 15
            reasons.append("MACD多头")
            if dif > 0:
                score += 5

        rsi = latest.get("RSI")
        if pd.notna(rsi) and 40 <= float(rsi) <= 70:
            score += 10
            reasons.append("RSI健康区间")

        k_value = latest.get("KDJ_K")
        d_value = latest.get("KDJ_D")
        if pd.notna(k_value) and pd.notna(d_value) and k_value > d_value:
            score += 10
            reasons.append("KDJ多头")

        ma5 = latest.get("MA5")
        ma10 = latest.get("MA10")
        previous_ma5 = previous.get("MA5")
        previous_ma10 = previous.get("MA10")
        if pd.notna(ma5) and pd.notna(ma10) and ma5 > ma10:
            score += 10
            reasons.append("短期均线支持")
            if (pd.notna(previous_ma5) and pd.notna(previous_ma10)
                    and previous_ma5 <= previous_ma10):
                score += 5
                reasons.append("短期金叉")

        volume = float(latest.get("volume") or 0)
        volume_ma5 = float(latest.get("VOL_MA5") or 0)
        volume_ratio = volume / volume_ma5 if volume_ma5 > 0 else 0
        if volume_ratio >= 1.2 and close >= float(latest.get("open") or close):
            score += 10
            reasons.append("量价配合")

        return {
            "technical_available": True,
            "technical_score": round(_clip(score)),
            "technical_reason": "；".join(reasons) if reasons else "技术信号偏弱",
            "trend_confirmation": trend,
            "volume_ratio": round(volume_ratio, 2),
        }

    def _composite_score(self, item: dict) -> int:
        weights = LONG_TERM["composite_weights"]
        return round(
            float(item.get("fundamental_score", 0)) * weights["fundamental"] +
            float(item.get("technical_score", 0)) * weights["technical"]
        )

    def _select_recommendations(self, ranked: list, ai_advisor=None) -> tuple:
        if not ranked:
            return [], [], {
                "available": False,
                "mode": "rule_only",
                "reason": "没有通过综合评分的候选股票。",
                "boards": [],
            }
        news = self.df.get_financial_news()
        external_news = (
            self.df.get_external_news()
            if hasattr(self.df, "get_external_news") else news
        )
        combined_news = []
        seen_news = set()
        for item in [*external_news, *news]:
            key = item.get("url") or item.get("title")
            if key and key not in seen_news:
                seen_news.add(key)
                combined_news.append(item)
        external_context = {}
        if hasattr(self.df, "get_external_market_context"):
            external_context = self.df.get_external_market_context(news=external_news)
        rotation_kwargs = {
            "top_n": LONG_TERM["theme_board_limit"],
        }
        if external_context:
            rotation_kwargs["external_context"] = external_context
        try:
            rotation = self.df.get_rotation_matches(
                [item["code"] for item in ranked], **rotation_kwargs
            )
        except TypeError as exc:
            if "external_context" not in str(exc):
                raise
            rotation_kwargs.pop("external_context", None)
            rotation = self.df.get_rotation_matches(
                [item["code"] for item in ranked], **rotation_kwargs
            )
        matches = rotation.get("matches", {})
        boards = rotation.get("boards", [])
        market_data_available = bool(boards)
        context = self.df.get_market_context()
        context["external_market"] = external_context
        rotation_analysis = _build_rule_rotation_analysis(context, boards)
        if ai_advisor is not None and ai_advisor.is_configured and boards:
            self._progress.update({
                "state": "rotation_ai",
                "message": "结合每日资金、国际环境与政策研判板块轮动",
            })
            try:
                rotation_analysis = ai_advisor.analyze_sector_rotation(
                    context=context,
                    news=combined_news,
                    boards=boards,
                )
            except Exception as exc:
                rotation_analysis = {
                    **_build_rule_rotation_analysis(context, boards),
                    "mode": "rule_fallback",
                    "reason": f"AI板块轮动分析失败，已回退资金+外部规则评分：{exc}",
                }

        if rotation_analysis.get("available"):
            rotation_analysis.setdefault("external_market", external_context)
        else:
            fallback_reason = rotation_analysis.get("reason", "")
            fallback_mode = rotation_analysis.get("mode", "rule_external")
            rotation_analysis = {
                **_build_rule_rotation_analysis(context, boards),
                "mode": fallback_mode,
                "reason": fallback_reason or "AI不可用，使用资金+外盘+事件规则评分。",
            }

        ai_boards = {
            board["name"]: board
            for board in rotation_analysis.get("boards", [])
            if isinstance(board, dict) and board.get("name")
        }
        ai_weight = float(LONG_TERM.get("rotation_ai_weight", 0.25))
        external_weight = float(LONG_TERM.get("rotation_external_weight", 0.20))
        for board in boards:
            rule_score = float(board.get("flow_score") or 0)
            board["rule_flow_score"] = round(rule_score)
            if external_context.get("available"):
                external_score = float(board.get("external_score") or 50)
                rule_score = (
                    rule_score * (1 - external_weight) +
                    external_score * external_weight
                )
            board["rule_external_score"] = round(rule_score)
            ai_board = ai_boards.get(board.get("name"))
            if rotation_analysis.get("available") and ai_board:
                ai_score = float(ai_board.get("rotation_score") or 0)
                board["ai_rotation_score"] = round(ai_score)
                board["ai_state"] = ai_board.get("state", "待确认")
                board["ai_confidence"] = ai_board.get("confidence", "低")
                board["ai_reason"] = ai_board.get("reason", "")
                board["ai_trigger"] = ai_board.get("trigger", "")
                board["ai_invalidation"] = ai_board.get("invalidation", "")
                board["rotation_score"] = round(
                    rule_score * (1 - ai_weight) + ai_score * ai_weight
                )
            else:
                board["rotation_score"] = round(rule_score)
        boards.sort(key=lambda board: board.get("rotation_score", 0), reverse=True)

        weights = LONG_TERM["selection_weights"]
        for item in ranked:
            stock_matches = sorted(
                matches.get(item["code"], []),
                key=lambda board: board.get("rotation_score", board.get("flow_score", 0)),
                reverse=True,
            )
            individual_score = _clip(50 + float(item.get("main_net_pct") or 0) * 3)
            if stock_matches:
                theme_score = float(
                    stock_matches[0].get("rotation_score", stock_matches[0].get("flow_score", 0))
                )
                market_score = theme_score * 0.8 + individual_score * 0.2
            elif market_data_available:
                market_score = individual_score * 0.3
            else:
                market_score = 50
            item["market_flow_score"] = round(_clip(market_score))
            item["matched_themes"] = []
            for board in stock_matches[:3]:
                recent_flow = board.get("recent_main_net_inflow")
                recent_text = (
                    f"近5日净流入{recent_flow:+.2f}亿"
                    if recent_flow is not None else
                    f"当日净流入{board.get('main_net_inflow', 0):+.2f}亿"
                )
                ai_text = ""
                if board.get("ai_state"):
                    ai_text = f"，AI:{board['ai_state']}/{board.get('ai_confidence', '低')}"
                external_text = ""
                if board.get("external_signal_count"):
                    reasons = "/".join(board.get("external_reasons", [])[:2])
                    external_text = (
                        f"，外部{board.get('external_score', 50):.0f}分"
                        f":{reasons or '事件映射'}"
                    )
                item["matched_themes"].append(
                    f"{board['name']}({board['type']}, {recent_text}{external_text}{ai_text})"
                )
            item["selection_score"] = round(
                item["composite_score"] * weights["composite"] +
                item["market_flow_score"] * weights["market_flow"]
            )
        selected = sorted(
            ranked,
            key=lambda item: (item["selection_score"], item["composite_score"]),
            reverse=True,
        )
        return selected, boards, rotation_analysis
