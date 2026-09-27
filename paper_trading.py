"""Forward-only paper ledger. One scheduled writer; no broker orders or backfill."""
from __future__ import annotations

import copy
import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path

from pullback_strategy import SHANGHAI, entry_gate
from trading_calendar import market_closed_reason
from weekly_strategy import build_holding_action, position_plan, week_identity, write_json

STATE_FILE = Path(os.getenv("PAPER_STATE_FILE", "output/research/paper_trading.json"))
MODES = {
    "aggressive": {"name": "小资金全仓模式", "initial": 50000, "goal": "短期翻倍目标 · 高波动实验"},
    "balanced": {"name": "大资金分仓模式", "initial": 500000, "goal": "稳定盈利目标 · 控制回撤"},
}
# Explicit simulation assumptions, not a broker fee quotation.
SLIPPAGE = 0.001
COMMISSION = 0.0003
SELL_TAX = 0.0005


def new_state():
    return {"version": 1, "updated_at": None, "status": "尚未开始，等待交易日行情",
            "assumptions": "前向模拟，每交易日13:05尝试一次；不补造历史成交。佣金万三、最低5元；卖出税费万五；双向滑点0.1%。价格限制附近保守不成交；不模拟真实排队和冲击成本。",
            "accounts": {key: {**spec, "cash": spec["initial"], "equity": spec["initial"],
                                  "holdings": [], "trades": [], "curve": [], "decisions": [],
                                  "max_drawdown_pct": 0, "return_pct": 0, "fees": 0,
                                  "week_id": None, "week_start_equity": spec["initial"]}
                         for key, spec in MODES.items()}}


def load_state(path=STATE_FILE):
    if not path.exists():
        return new_state()
    # A corrupt ledger must fail visibly, never silently reset capital.
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1 or set(data.get("accounts", {})) != set(MODES):
        raise ValueError("模拟仓账本格式不兼容")
    return data


def fee(value, side):
    return round(max(5, value * COMMISSION) + (value * SELL_TAX if side == "sell" else 0), 2)


def fresh_quote(quote, now):
    try:
        day = now.strftime("%Y%m%d")
        clock = str(quote["last_time"]).replace(":", "")
        stamp = datetime.strptime(day + clock, "%Y%m%d%H%M").replace(tzinfo=SHANGHAI)
        return (quote.get("available") and str(quote["trade_date"]).replace("-", "") == day
                and 0 <= (now - stamp).total_seconds() <= 300
                and all(math.isfinite(float(quote[k])) and float(quote[k]) > 0 for k in ("close_price", "low")))
    except (KeyError, ValueError, TypeError):
        return False


def fill_price(code, name, quote, trend, side):
    """Conservative daily-limit filter using the previous completed close."""
    previous = float(trend.get("close") or 0)
    if previous <= 0:
        return None
    limit = 0.05 if "ST" in name.upper() else 0.2 if code.startswith(("30", "68")) else 0.3 if code.startswith(("4", "8", "92")) else 0.1
    price = round(float(quote["close_price"]) * (1 + SLIPPAGE if side == "buy" else 1 - SLIPPAGE), 2)
    # No fills near either daily limit; new listings/unknown special rules need review.
    if not previous * (1 - limit + 0.002) < price < previous * (1 + limit - 0.002):
        return None
    return price


def account_context(book, mode):
    equity = book["cash"] + sum(h["quantity"] * h["current_price"] for h in book["holdings"])
    frozen = equity <= book["week_start_equity"] * 0.98
    return {"equity": equity, "available_cash": book["cash"], "holdings_value": equity - book["cash"],
            "current_week_frozen": frozen, "can_open_new": not frozen,
            "block_reasons": ["本周亏损达到2%，暂停新开仓"] if frozen else [],
            "risk_profile": {"max_total_pct": 0.6, "max_stock_pct": 0.2,
                             "risk_per_trade": equity * 0.006},
            "broker_conditional_orders": "未确认"}


def execute_session(state, plan, quotes, holding_reviews, now):
    """Pure, deterministic accounting; quotes/reviews are gathered separately."""
    state = copy.deepcopy(state)
    now = now.astimezone(SHANGHAI)
    day, week = now.date().isoformat(), week_identity(now)["plan_id"]
    closed = market_closed_reason(now.date())
    if closed or not "13:00" <= now.strftime("%H:%M") < "14:00":
        state.update(status=closed or "等待13:00–14:00模拟窗口", updated_at=now.isoformat())
        return state
    if state.get("last_session") == day:
        return state
    selections = plan.get("selections", []) if plan.get("plan_id") == week and plan.get("frozen") else []
    expected = now.date() - timedelta(days=1)
    while market_closed_reason(expected):
        expected -= timedelta(days=1)
    for mode, book in state["accounts"].items():
        book["decisions"] = []
        note = book["decisions"].append
        if not book["curve"]:
            book["curve"].append({"date": now.isoformat(), "equity": book["initial"], "return_pct": 0, "drawdown_pct": 0})
        if book["week_id"] != week:
            book.update(week_id=week, week_start_equity=book["equity"])
        traded = set()
        marks_complete = True
        for holding in list(book["holdings"]):
            code = holding["code"]
            quote, review = quotes.get(code, {}), holding_reviews.get(code, {})
            trend = review.get("weekly_trend", {})
            if not fresh_quote(quote, now):
                note(f"{code} 行情过期或缺失，保留上次估值，不成交")
                marks_complete = False
                continue
            holding["current_price"] = float(quote["close_price"])
            holding["quote_as_of"] = day + " " + str(quote["last_time"])
            if not review.get("morning_ready") or str(trend.get("as_of", "")) != expected.isoformat():
                note(f"{code} 个股/大盘/行业或日K不完整，等待复核")
                continue
            if holding["bought_on"] >= day:
                note(f"{code} 当日买入，T+1不可卖")
                continue
            context = account_context(book, mode)
            action = build_holding_action(holding, {**trend, "close": holding["current_price"]}, context)
            qty, reason, stage = 0, action["reason"], holding["profit_stage"]
            if action["action"] in {"清仓", "至少减仓50%", "减仓或退出"}:
                qty = action["sell_quantity"]
            else:
                risk = holding["initial_risk"]
                target_stage = 2 if holding["current_price"] >= holding["cost_price"] + 2.5 * risk else 1 if holding["current_price"] >= holding["cost_price"] + 1.5 * risk else 0
                if target_stage > stage:
                    target = math.floor(holding["initial_quantity"] * target_stage / 3 / 100) * 100
                    qty = min(holding["quantity"], max(0, target - holding["profit_sold"]))
                    reason = f"达到{2.5 if target_stage == 2 else 1.5}R，累计兑现原始仓位{target_stage}/3"
                    stage = target_stage
            price = fill_price(code, holding["name"], quote, trend, "sell")
            if qty and price:
                value = round(qty * price, 2)
                costs = fee(value, "sell")
                book["cash"] = round(book["cash"] + value - costs, 2)
                book["fees"] = round(book["fees"] + costs, 2)
                holding["quantity"] -= qty
                if stage > holding["profit_stage"]:
                    holding["profit_sold"] += qty
                    holding["profit_stage"] = stage
                book["trades"].append({"time": now.isoformat(), "code": code, "name": holding["name"],
                                       "side": "sell", "quantity": qty, "price": price, "fee": costs,
                                       "reason": reason, "plan_id": plan.get("plan_id")})
                traded.add(code)
                if not holding["quantity"]:
                    book["holdings"].remove(holding)
            elif qty:
                note(f"{code} 价格限制附近，不假设能够卖出")
            else:
                note(f"{code} 持有；未触发新的可执行退出档位")
            # Apply completed-day structure only for the NEXT check, never retroactively.
            if holding["profit_stage"] and holding["quantity"]:
                holding["stop_price"] = max(holding["stop_price"], float(review.get("trailing_stop") or 0))
        for item in sorted(selections, key=lambda x: x.get("weekly_score") or 0, reverse=True):
            code = item["code"]
            if code in traded or any(h["code"] == code for h in book["holdings"]):
                continue
            if item.get("role") != "主选" or item.get("status") == "撤销":
                continue
            trend, quote = item.get("weekly_trend", {}), quotes.get(code, {})
            if not marks_complete:
                note(f"{code} 已有持仓估值缺失，暂停增加风险")
                continue
            if mode == "aggressive" and book["holdings"]:
                note(f"{code} 单股模式已有持仓，不另开仓")
                continue
            context = account_context(book, mode)
            if not context["can_open_new"]:
                note(context["block_reasons"][0])
                continue
            if not fresh_quote(quote, now):
                note(f"{code} 等待5分钟内有效行情")
                continue
            price = fill_price(code, item.get("name", ""), quote, trend, "buy")
            if price is None:
                note(f"{code} 价格限制附近，跳过买入")
                continue
            gate = entry_gate(trend, {**quote, "close_price": price}, now)
            if not gate["passed"] or not trend.get("qualified"):
                note(f"{code} {gate['reason']}")
                continue
            quantity = math.floor(book["cash"] / price / 100) * 100 if mode == "aggressive" else position_plan(trend, context, "主选", gate=gate, code=code)["quantity"]
            while quantity > 0 and quantity * price + fee(quantity * price, "buy") > book["cash"]:
                quantity -= 100
            minimum = 200 if code.startswith("688") else 100
            if quantity < minimum:
                note(f"{code} 资金或风险预算不足最小买入量")
                continue
            value = round(quantity * price, 2)
            costs = fee(value, "buy")
            book["cash"] = round(book["cash"] - value - costs, 2)
            book["fees"] = round(book["fees"] + costs, 2)
            book["holdings"].append({"code": code, "name": item.get("name", ""), "quantity": quantity,
                                     "initial_quantity": quantity, "cost_price": price,
                                     "current_price": float(quote["close_price"]), "stop_price": trend["stop_price"],
                                     "initial_risk": price - trend["stop_price"], "bought_on": day,
                                     "profit_stage": 0, "profit_sold": 0, "quote_as_of": day + " " + str(quote["last_time"])})
            book["trades"].append({"time": now.isoformat(), "code": code, "name": item.get("name", ""),
                                   "side": "buy", "quantity": quantity, "price": price, "fee": costs,
                                   "reason": "周度主选 + 上升趋势 + 回调企稳 + 实时价格复核通过", "plan_id": plan["plan_id"]})
        if not selections:
            note("没有当周已冻结候选名单，保持持仓管理，不新开仓")
        book["equity"] = round(book["cash"] + sum(h["quantity"] * h["current_price"] for h in book["holdings"]), 2)
        peak = max([book["initial"], book["equity"]] + [p["equity"] for p in book["curve"]])
        drawdown = round((1 - book["equity"] / peak) * 100, 3)
        book["max_drawdown_pct"] = max(book["max_drawdown_pct"], drawdown)
        book["return_pct"] = round((book["equity"] / book["initial"] - 1) * 100, 3)
        book["valuation_complete"] = marks_complete
        book["curve"].append({"date": now.isoformat(), "equity": book["equity"], "return_pct": book["return_pct"], "drawdown_pct": drawdown, "complete": marks_complete})
    state.update(last_session=day, updated_at=now.isoformat(), status="本次模拟已完成；同一交易日不重复成交")
    return state


def run():
    from data_feed import DataFeed
    from research_core import build_push_payload, build_account_holding_actions
    import pandas as pd

    state = load_state()
    now = datetime.now(SHANGHAI)
    if market_closed_reason(now.date()) or not "13:00" <= now.strftime("%H:%M") < "14:00" or state.get("last_session") == now.date().isoformat():
        write_json(STATE_FILE, execute_session(state, {}, {}, {}, now))
        return
    # Never load the user's real account into the paper ledger.
    payload = build_push_payload(account_override={"equity": 500000, "available_cash": 500000,
        "holdings": [], "holdings_value": 0, "can_open_new": True, "block_reasons": [],
        "risk_profile": {"max_total_pct": 0.6, "max_stock_pct": 0.2, "risk_per_trade": 3000}})
    plan = payload["weekly_plan"]
    holdings = {h["code"]: h for book in state["accounts"].values() for h in book["holdings"]}
    feed = DataFeed()
    reviews = build_account_holding_actions({"holdings_tracking_enabled": True, "holdings": list(holdings.values())}, holding_feed=feed)
    review_by_code = {r["code"]: r for r in reviews}
    for code, review in review_by_code.items():
        frame = feed.get_kline(code, count=120)
        if frame is not None and not frame.empty:
            frame = frame[pd.to_datetime(frame["date"]) < pd.Timestamp(now.date())].sort_values("date")
            if len(frame) >= 10:
                review["trailing_stop"] = round(min(float(frame["close"].tail(10).mean()), float(frame["low"].tail(3).min())), 2)
    quotes = {}
    for code in set(holdings) | {s["code"] for s in plan.get("selections", [])}:
        try:
            quotes[code] = feed.get_intraday_minute(code)
        except Exception:
            quotes[code] = {}
    state = execute_session(state, plan, quotes, review_by_code, datetime.now(SHANGHAI))
    write_json(STATE_FILE, state)


if __name__ == "__main__":
    run()
