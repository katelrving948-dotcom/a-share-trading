"""Frozen support plans, evaluated only with completed daily bars."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from config import LONG_TERM, PULLBACK


SHANGHAI = ZoneInfo("Asia/Shanghai")


def holding_horizon(market_cap: float) -> dict:
    small = 0 < market_cap < LONG_TERM["small_cap_boundary"]
    return {
        "style": "中小市值趋势波段" if small else "趋势波段",
        "min_sessions": 3 if small else 5,
        "max_sessions": 10 if small else 20,
        "review_after_sessions": 3 if small else 5,
        "label": "3-10个交易日" if small else "5-20个交易日",
        "rule": "从实际买入日起计；复核日仍未恢复强势则减仓或退出，破位提前退出，到期复核，不承诺涨幅或持有到期",
    }


def build_pullback_plan(bars: pd.DataFrame, ma20: float, atr: float, previous: dict | None = None) -> dict:
    last_date = str(pd.Timestamp(bars.iloc[-1]["date"]).date())
    previous = previous or {}
    if previous.get("version") == PULLBACK["version"]:
        plan = dict(previous)
    else:
        support_low = round(ma20 - atr * PULLBACK["support_atr"], 2)
        support_high = round(ma20 + atr * PULLBACK["support_atr"], 2)
        plan = {
            "version": PULLBACK["version"], "formed_on": last_date,
            "support_low": support_low, "support_high": support_high,
            "anchor_high": round(float(bars["high"].tail(10).max()), 2),
            "anchor_atr": atr,
            "stop_price": round(float(bars["low"].tail(10).min()) - atr * PULLBACK["stop_buffer_atr"], 2),
            "max_entry_price": round(support_high + atr * PULLBACK["max_entry_atr"], 2),
            "valid_sessions": PULLBACK["valid_sessions"],
            "state": "waiting_pullback", "touched_on": None, "confirmed_on": None,
        }
    # Replay from the fixed anchor: repeated previews cannot move the support or
    # invent a touch/confirmation that happened before the plan existed.
    later = bars[pd.to_datetime(bars["date"]).dt.strftime("%Y-%m-%d") > plan["formed_on"]]
    if plan.get("state") in {"invalidated", "expired"}:
        return plan
    plan.update(state="waiting_pullback", touched_on=None, confirmed_on=None)
    touch_low = None
    for age, (_, row) in enumerate(later.iterrows(), 1):
        day = str(pd.Timestamp(row["date"]).date())
        low, high, close = float(row["low"]), float(row["high"]), float(row["close"])
        if low <= plan["stop_price"]:
            plan.update(state="invalidated", invalidated_on=day)
            break
        if age >= plan["valid_sessions"]:
            plan.update(state="expired")
            break
        touch = low <= plan["support_high"] and high >= plan["support_low"]
        retraced = plan["anchor_high"] - low >= plan["anchor_atr"] * PULLBACK["minimum_retrace_atr"]
        if plan["confirmed_on"]:
            if close < plan["support_low"]:
                plan.update(state="invalidated", invalidated_on=day)
                break
            continue
        if touch_low is not None and low >= touch_low and close > plan["support_high"]:
            plan.update(state="confirmed", confirmed_on=day)
        elif touch and retraced:
            plan.update(state="waiting_confirmation", touched_on=day)
            touch_low = low
    plan["elapsed_sessions"] = len(later)
    plan["as_of"] = last_date
    return plan


def entry_gate(trend: dict, quote: dict | None, now: datetime) -> dict:
    """A daily confirmation is a conditional plan, never a live fill."""
    quote = quote or {}
    plan = trend.get("pullback_plan") or {}
    state = plan.get("state", "missing")
    labels = {
        "missing": "等待回调计划", "waiting_pullback": "等待回调",
        "waiting_confirmation": "等待企稳", "confirmed": "企稳已确认，等待价格复核",
        "invalidated": "回调计划失效", "expired": "回调计划已到期",
    }
    result = {"passed": False, "state": state, "reason": labels.get(state, "等待复核"), "reference_price": None}
    if not trend.get("trend_qualified"):
        result.update(state="trend_failed", reason="上升趋势未通过")
        return result
    if state != "confirmed":
        return result
    current = now.astimezone(SHANGHAI) if now.tzinfo else now.replace(tzinfo=SHANGHAI)
    today = current.strftime("%Y-%m-%d")
    if not plan.get("confirmed_on") or plan["confirmed_on"] >= today:
        result.update(reason="日K企稳信号最早下一交易日执行")
        return result
    expected = current.date() - timedelta(days=1)
    while expected.weekday() >= 5:
        expected -= timedelta(days=1)
    if str(trend.get("as_of") or "") < expected.isoformat():
        result.update(reason="已完成日K未更新到上一工作日，等待行情复核（长假后需补齐交易日数据）")
        return result
    if not quote.get("available") or str(quote.get("trade_date", "")).replace("-", "") != current.strftime("%Y%m%d"):
        result.update(reason="等待当日有效行情，不使用历史收盘价作为买入许可")
        return result
    try:
        price = float(quote["close_price"])
        day_low = float(quote["low"])
        last_time = str(quote["last_time"])
        if len(last_time) == 4 and last_time.isdigit():
            last_time = last_time[:2] + ":" + last_time[2:]
        stamp = datetime.strptime(today + " " + last_time, "%Y-%m-%d %H:%M").replace(tzinfo=SHANGHAI)
        if not all(pd.notna(x) and 0 < x < float("inf") for x in (price, day_low)):
            raise ValueError("invalid quote")
    except (KeyError, ValueError, TypeError):
        result.update(reason="行情价格或时间戳缺失，等待复核")
        return result
    result["reference_price"] = price
    if day_low <= plan["stop_price"]:
        result.update(state="invalidated", reason="当日已触及结构失效位，取消原计划")
        return result
    if price > plan["max_entry_price"] or price < plan["support_low"]:
        result.update(reason="当前价格不在允许买入区，继续等待")
        return result
    risk = (price - plan["stop_price"]) / price
    reward_risk = (plan["anchor_high"] - price) / (price - plan["stop_price"]) if price > plan["stop_price"] else 0
    result.update(risk_pct=round(risk * 100, 2), reward_risk=round(reward_risk, 2))
    if not PULLBACK["minimum_risk_pct"] <= risk <= PULLBACK["maximum_risk_pct"] or reward_risk < PULLBACK["minimum_reward_risk"]:
        result.update(reason="按当前买价计算的止损距离或到前高的收益空间不足")
        return result
    hm = current.strftime("%H:%M")
    trading = current.weekday() < 5 and ("09:30" <= hm <= "11:30" or "13:00" <= hm < "15:00")
    if not trading or not 0 <= (current - stamp).total_seconds() <= 300:
        result.update(reason="条件计划已形成，待交易时段用5分钟内行情复核")
        return result
    result.update(passed=True, reason="基本价格条件通过：回调企稳、买价和风险合格")
    return result
