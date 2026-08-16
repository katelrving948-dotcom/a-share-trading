"""Transparent fundamental scoring for the research dashboard and email digest."""

from __future__ import annotations

from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd

from config import LONG_TERM, SCREEN
from data_feed import DataFeed


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _growth_score(value: float) -> float:
    if value <= -30:
        return 0
    if value < 0:
        return 30 + value
    if value <= 20:
        return 50 + value * 2
    if value <= 50:
        return 90 + (value - 20) / 3
    return 100


class FundamentalScorer:
    """Score disclosed financial metrics without technical or trading rules."""

    def __init__(self, data_feed: DataFeed | None = None):
        self.data_feed = data_feed or DataFeed()
        self.progress = {"state": "idle", "done": 0, "total": 0}
        self.summary: dict = {}

    def score(
        self,
        universe_limit: int | None = None,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> list[dict]:
        stocks = self.data_feed.get_stock_list()
        if stocks.empty:
            self.summary = {"error": "获取市场股票池失败", "generated_at": self._now()}
            return []

        pool = self._candidate_pool(stocks)
        limit = LONG_TERM.get("universe_limit", 0) if universe_limit is None else int(universe_limit)
        if limit > 0:
            pool = pool.head(limit)
        codes = pool["code"].astype(str).tolist()
        self._set_progress("fundamentals", 0, len(codes), "逐股读取已披露财务指标", progress_callback)

        def on_financial_progress(done, total, code, available):
            self._set_progress(
                "fundamentals", done, total,
                f"读取 {code}：{'成功' if available else '无有效数据'}",
                progress_callback,
            )

        financials = self.data_feed.get_financials_batch(codes, progress_callback=on_financial_progress)
        rows = []
        for code in codes:
            financial = financials.get(str(code), {})
            if financial.get("available"):
                rows.append(self._evaluate(financial))
        rows.sort(key=lambda item: item["fundamental_score"], reverse=True)
        for rank, item in enumerate(rows, start=1):
            item["fundamental_rank"] = rank

        minimum = float(LONG_TERM.get("minimum_score", 50))
        self.summary = {
            "generated_at": self._now(),
            "pool_count": len(pool),
            "financial_success_count": len(rows),
            "qualified_count": sum(item["fundamental_score"] >= minimum for item in rows),
            "minimum_score": minimum,
            "weights": dict(LONG_TERM["weights"]),
            "scan_scope": "全部初筛股票" if limit <= 0 else f"流动性前 {limit} 只",
            "data_source": "东方财富已披露财务指标与行情估值快照",
            "purpose": "基本面评分可视化；不生成买卖、仓位或止盈止损计划",
        }
        self._set_progress("done", len(codes), len(codes), "基本面评分完成", progress_callback)
        return rows

    def _set_progress(self, state, done, total, message, callback):
        self.progress = {"state": state, "done": done, "total": total, "message": message}
        if callback:
            callback(dict(self.progress))

    @staticmethod
    def _now() -> str:
        return datetime.now(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _candidate_pool(stocks: pd.DataFrame) -> pd.DataFrame:
        conditions = (
            (stocks["price"] >= SCREEN["price_min"])
            & (stocks["price"] <= SCREEN["price_max"])
            & (stocks["market_cap"] >= LONG_TERM["market_cap_min"])
            & (stocks["amount"] >= LONG_TERM["average_amount_min"] * 1e8)
            & (stocks["turnover_rate"] <= LONG_TERM["turnover_max"])
        )
        if SCREEN.get("exclude_st"):
            conditions &= ~stocks["is_st"]
        if SCREEN.get("exclude_kcb"):
            conditions &= stocks["board"] != "科创板"
        if SCREEN.get("exclude_bj"):
            conditions &= stocks["board"] != "北交所"
        return stocks[conditions].copy().sort_values("amount", ascending=False)

    @staticmethod
    def _evaluate(financial: dict) -> dict:
        roe = float(financial.get("annualized_roe") or 0)
        revenue_growth = float(financial.get("revenue_growth") or 0)
        profit_growth = float(financial.get("profit_growth") or 0)
        gross_margin = financial.get("gross_margin")
        eps = float(financial.get("eps") or 0)
        cash_per_share = float(financial.get("operating_cf_per_share") or 0)
        pe = float(financial.get("pe") or 0)
        pb = float(financial.get("pb") or 0)

        roe_score = _clip(roe * 5)
        margin_score = _clip(float(gross_margin) * 2) if gross_margin is not None else roe_score
        quality = round(roe_score * 0.7 + margin_score * 0.3)
        growth = round(_growth_score(revenue_growth) * 0.4 + _growth_score(profit_growth) * 0.6)
        pe_score = 10 if pe <= 0 else 90 if pe <= 12 else 78 if pe <= 25 else 52 if pe <= 40 else 28 if pe <= 70 else 10
        pb_score = 35 if pb <= 0 else 90 if pb <= 2 else 65 if pb <= 5 else 40 if pb <= 10 else 18
        valuation = round(pe_score * 0.65 + pb_score * 0.35)
        cashflow = 10 if eps <= 0 and cash_per_share >= 0 else 0 if eps <= 0 else round(_clip(cash_per_share / eps * 70))
        weights = LONG_TERM["weights"]
        total = round(
            quality * weights["quality"] + growth * weights["growth"]
            + valuation * weights["valuation"] + cashflow * weights["cashflow"]
        )

        risks = []
        if eps <= 0:
            risks.append("每股收益非正")
        if revenue_growth < 0 or profit_growth < 0:
            risks.append("盈利增长承压")
        if pe <= 0 or pe > 50:
            risks.append("估值异常或偏高")
        if eps > 0 and cash_per_share < eps * 0.5:
            risks.append("经营现金流覆盖偏低")
        if gross_margin is None:
            risks.append("毛利率未披露或不适用")

        return {
            "code": str(financial["code"]).zfill(6), "name": financial.get("name", ""),
            "industry": financial.get("industry") or financial.get("board", ""),
            "report_date": financial.get("report_date", ""), "notice_date": financial.get("notice_date", ""),
            "price": financial.get("price", 0), "market_cap": financial.get("market_cap", 0),
            "fundamental_score": total, "quality_score": quality, "growth_score": growth,
            "valuation_score": valuation, "cashflow_score": cashflow,
            "roe": financial.get("roe", 0), "annualized_roe": roe,
            "revenue_growth": revenue_growth, "profit_growth": profit_growth,
            "gross_margin": gross_margin, "eps": eps, "operating_cf_per_share": cash_per_share,
            "pe": pe, "pb": pb,
            "risk": "；".join(risks) if risks else "未触发财务量化警示，仍需核验公告",
            "data_source": financial.get("data_source", ""),
        }


LongTermFundamentalScreener = FundamentalScorer
