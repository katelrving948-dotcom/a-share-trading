"""Weekly trend plan, account risk gates, and frozen candidate selection."""

from __future__ import annotations

import json
import math
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd


ACCOUNT_STATE_FILE = Path("output/research/account_state.json")
WEEKLY_PLAN_FILE = Path("output/research/weekly_plan.json")

WEEKLY_WEIGHTS = {
    "fundamental": 0.40,
    "trend": 0.30,
    "board": 0.15,
    "external_event": 0.10,
    "valuation_crowding": 0.05,
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def week_identity(now: datetime | date) -> dict:
    current = now.date() if isinstance(now, datetime) else now
    iso_year, iso_week, _ = current.isocalendar()
    monday = current - timedelta(days=current.weekday())
    friday = monday + timedelta(days=4)
    return {
        "plan_id": f"{iso_year}-W{iso_week:02d}",
        "week_start": monday.isoformat(),
        "week_end": friday.isoformat(),
    }


def load_account_state(path: Path = ACCOUNT_STATE_FILE, now: datetime | None = None) -> dict:
    current = now or datetime.now()
    raw = _read_json(path)
    private_state = os.getenv("ACCOUNT_STATE_JSON", "").strip()
    if private_state:
        try:
            decoded = json.loads(private_state)
            if isinstance(decoded, dict):
                raw = decoded
        except json.JSONDecodeError:
            raw = {**raw, "account_state_error": "ACCOUNT_STATE_JSON 格式无效"}
    equity = max(1.0, _number(raw.get("equity"), 50000.0))
    # Per-stock holdings are intentionally outside the current stage. Ignore
    # legacy/private holdings fields until that module is explicitly enabled.
    holdings = []
    last_week_pnl = _number(raw.get("last_week_pnl"), 0.0)
    current_week_pnl = _number(raw.get("current_week_pnl"), 0.0)
    last_week_end = str(raw.get("last_week_end") or "")
    try:
        loss_week_end = date.fromisoformat(last_week_end)
    except ValueError:
        loss_week_end = None
    identity = week_identity(current)
    current_monday = date.fromisoformat(identity["week_start"])
    recovery_week = bool(
        last_week_pnl / equity <= -0.02
        and loss_week_end
        and current_monday == loss_week_end + timedelta(days=3)
    )
    current_week_frozen = current_week_pnl / equity <= -0.02

    market_state = str(raw.get("market_state") or "普通").strip()
    if recovery_week:
        profile = {"name": "恢复期", "max_total_pct": 0.30, "max_stock_pct": 0.15, "risk_per_trade": 200.0}
    elif market_state == "强势":
        profile = {"name": "强势", "max_total_pct": 0.70, "max_stock_pct": 0.25, "risk_per_trade": 300.0}
    elif market_state in {"弱势", "事件不确定"}:
        profile = {"name": market_state, "max_total_pct": 0.30, "max_stock_pct": 0.15, "risk_per_trade": 200.0}
    else:
        profile = {"name": "普通", "max_total_pct": 0.60, "max_stock_pct": 0.20, "risk_per_trade": 300.0}

    holdings_value = 0.0
    holdings_risk = 0.0
    normalized_holdings = []
    for holding in holdings:
        if not isinstance(holding, dict):
            continue
        quantity = max(0, int(_number(holding.get("quantity"))))
        cost = max(0.0, _number(holding.get("cost_price")))
        current_price = max(0.0, _number(holding.get("current_price"), cost))
        stop_price = max(0.0, _number(holding.get("stop_price")))
        market_value = round(quantity * current_price, 2)
        planned_risk = round(max(0.0, current_price - stop_price) * quantity, 2) if stop_price else None
        holdings_value += market_value
        holdings_risk += planned_risk or 0.0
        normalized_holdings.append({
            "code": str(holding.get("code") or "").zfill(6),
            "name": str(holding.get("name") or ""),
            "quantity": quantity,
            "cost_price": round(cost, 2),
            "current_price": round(current_price, 2),
            "stop_price": round(stop_price, 2) if stop_price else None,
            "market_value": market_value,
            "planned_risk": planned_risk,
        })

    reasons = []
    if current_week_frozen:
        reasons.append("本周亏损已达到总资金2%，停止新增风险")
    if holdings_value > equity * profile["max_total_pct"]:
        reasons.append("现有持仓已超过当前风险档位总仓上限")

    return {
        "equity": round(equity, 2),
        "available_cash": round(max(0.0, _number(raw.get("available_cash"), equity - holdings_value)), 2),
        "last_week_pnl": round(last_week_pnl, 2),
        "last_week_return_pct": round(last_week_pnl / equity * 100, 2),
        "last_week_end": last_week_end or None,
        "current_week_pnl": round(current_week_pnl, 2),
        "current_week_return_pct": round(current_week_pnl / equity * 100, 2),
        "holdings_tracking_enabled": False,
        "holdings": normalized_holdings,
        "holdings_value": round(holdings_value, 2),
        "holdings_pct": round(holdings_value / equity * 100, 2),
        "holdings_planned_risk": round(holdings_risk, 2),
        "broker_conditional_orders": str(raw.get("broker_conditional_orders") or "待确认"),
        "risk_profile": profile,
        "recovery_week": recovery_week,
        "current_week_frozen": current_week_frozen,
        "can_open_new": not reasons,
        "block_reasons": reasons,
        "updated_at": raw.get("updated_at"),
    }


def validate_account_update(payload: dict, existing: dict | None = None) -> dict:
    base = dict(existing or {})
    allowed = {
        "equity", "available_cash", "last_week_pnl", "last_week_end",
        "current_week_pnl",
        "broker_conditional_orders", "market_state",
    }
    for key in allowed:
        if key in payload:
            base[key] = payload[key]
    equity = _number(base.get("equity"), 50000)
    if equity <= 0:
        raise ValueError("账户净值必须大于0")
    base["equity"] = round(equity, 2)
    base["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return base


def save_account_update(payload: dict, path: Path = ACCOUNT_STATE_FILE) -> dict:
    updated = validate_account_update(payload, _read_json(path))
    write_json(path, updated)
    return load_account_state(path)


def _atr(frame: pd.DataFrame, period: int = 14) -> float:
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    true_range = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return _number(true_range.tail(period).mean())


def analyze_weekly_trend(frame: pd.DataFrame, benchmark: pd.DataFrame | None = None) -> dict:
    if frame is None or frame.empty or len(frame) < 60:
        return {"available": False, "qualified": False, "reason": "日K数据不足60个交易日"}
    ordered = frame.sort_values("date").reset_index(drop=True).copy()
    close = ordered["close"].astype(float)
    latest = ordered.iloc[-1]
    latest_close = _number(latest.get("close"))
    if latest_close <= 0:
        return {"available": False, "qualified": False, "reason": "最新收盘价无效"}
    ma20 = _number(close.tail(20).mean())
    ma60 = _number(close.tail(60).mean())
    ma20_previous = _number(close.iloc[-25:-5].mean()) if len(close) >= 25 else ma20
    atr = _atr(ordered)
    atr_pct = atr / latest_close if latest_close else 0.0
    return_20 = latest_close / _number(close.iloc[-21], latest_close) - 1 if len(close) > 20 else 0.0
    benchmark_return_20 = 0.0
    if benchmark is not None and not benchmark.empty and len(benchmark) > 20:
        bench_close = benchmark.sort_values("date")["close"].astype(float)
        benchmark_return_20 = _number(bench_close.iloc[-1]) / _number(bench_close.iloc[-21], 1) - 1
    relative_strength = return_20 - benchmark_return_20
    recent_low = _number(ordered["low"].astype(float).tail(10).min(), latest_close)
    extension_pct = (latest_close / ma20 - 1) if ma20 else 0.0
    overextended = bool(
        extension_pct > max(0.08, atr_pct * 2)
        or return_20 > max(0.18, atr_pct * 5)
    )
    trend_checks = {
        "above_ma20": latest_close > ma20,
        "ma20_above_ma60": ma20 > ma60,
        "ma20_rising": ma20 > ma20_previous,
        "relative_strength_positive": relative_strength > 0,
    }
    trend_score = 20.0
    trend_score += 20 if trend_checks["above_ma20"] else 0
    trend_score += 20 if trend_checks["ma20_above_ma60"] else 0
    trend_score += 20 if trend_checks["ma20_rising"] else 0
    trend_score += min(20, max(0, relative_strength * 200))
    if overextended:
        trend_score -= 20
    trend_score = round(max(0.0, min(100.0, trend_score)), 2)

    entry_high = latest_close
    entry_low = max(ma20, latest_close - atr * 0.5)
    if entry_low > entry_high:
        entry_low = entry_high
    entry_mid = (entry_low + entry_high) / 2
    structure_stop = recent_low - atr * 0.2
    stop_price = min(structure_stop, entry_mid * 0.96)
    stop_distance_pct = (entry_mid - stop_price) / entry_mid if entry_mid else 1.0
    risk_valid = 0.04 <= stop_distance_pct <= 0.07
    max_chase = min(latest_close + atr * 0.5, ma20 + atr * 2) if atr > 0 else latest_close * 1.02
    risk_per_share = max(0.0, entry_mid - stop_price)
    target_one = entry_mid + risk_per_share * 1.5
    target_two = entry_mid + risk_per_share * 2.5
    qualified = all(trend_checks.values()) and not overextended and risk_valid
    reasons = []
    if not all(trend_checks.values()):
        reasons.append("周度趋势或相对强度未完全确认")
    if overextended:
        reasons.append("价格偏离20日均线或20日涨幅过大，禁止追高")
    if not risk_valid:
        reasons.append(f"结构止损距离{stop_distance_pct:.1%}不在4%-7%范围")

    return {
        "available": True,
        "as_of": str(pd.Timestamp(latest.get("date")).date()),
        "qualified": qualified,
        "trend_score": trend_score,
        "close": round(latest_close, 2),
        "ma20": round(ma20, 2),
        "ma60": round(ma60, 2),
        "atr": round(atr, 3),
        "atr_pct": round(atr_pct * 100, 2),
        "return_20_pct": round(return_20 * 100, 2),
        "benchmark_return_20_pct": round(benchmark_return_20 * 100, 2),
        "relative_strength_pct": round(relative_strength * 100, 2),
        "extension_pct": round(extension_pct * 100, 2),
        "overextended": overextended,
        "checks": trend_checks,
        "entry_zone": {"low": round(entry_low, 2), "high": round(entry_high, 2)},
        "max_chase_price": round(max_chase, 2),
        "stop_price": round(stop_price, 2),
        "stop_distance_pct": round(stop_distance_pct * 100, 2),
        "take_profit": [
            {"name": "第一止盈", "price": round(target_one, 2), "sell_fraction": "1/3", "risk_reward": 1.5},
            {"name": "第二止盈", "price": round(target_two, 2), "sell_fraction": "1/3", "risk_reward": 2.5},
        ],
        "time_stop": "5个交易日未出现相对强势，减仓或退出",
        "trailing_rule": "剩余仓位按10日线、前低或周线结构跟踪；止损不得下移",
        "reasons": reasons,
    }


def position_plan(trend: dict, account: dict, role: str, reserved_value: float = 0.0) -> dict:
    entry = trend.get("entry_zone") or {}
    entry_mid = (_number(entry.get("low")) + _number(entry.get("high"))) / 2
    stop = _number(trend.get("stop_price"))
    per_share_risk = max(0.0, entry_mid - stop)
    profile = account["risk_profile"]
    equity = _number(account.get("equity"))
    available_cash = _number(account.get("available_cash"))
    remaining_total = max(0.0, equity * profile["max_total_pct"] - _number(account.get("holdings_value")) - reserved_value)
    maximum_stock_value = equity * profile["max_stock_pct"]
    risk_quantity = math.floor(profile["risk_per_trade"] / per_share_risk / 100) * 100 if per_share_risk > 0 else 0
    stock_cap_quantity = math.floor(maximum_stock_value / entry_mid / 100) * 100 if entry_mid > 0 else 0
    total_cap_quantity = math.floor(min(remaining_total, available_cash) / entry_mid / 100) * 100 if entry_mid > 0 else 0
    quantity = max(0, min(risk_quantity, stock_cap_quantity, total_cap_quantity))
    estimated_value = round(quantity * entry_mid, 2)
    planned_loss = round(quantity * per_share_risk, 2)
    executable = bool(account.get("can_open_new") and role == "主选" and quantity >= 100 and trend.get("qualified"))
    reasons = list(account.get("block_reasons") or [])
    if role == "备选":
        reasons.append("备选股只有在主选撤销后才能启用")
    if quantity < 100:
        reasons.append("100股最小交易单位超过当前仓位或风险预算")
    if not trend.get("qualified"):
        reasons.extend(trend.get("reasons") or ["周度趋势未通过"])
    return {
        "quantity": quantity,
        "estimated_value": estimated_value,
        "position_pct": round(estimated_value / equity * 100, 2) if equity else 0.0,
        "planned_loss": planned_loss,
        "risk_budget": profile["risk_per_trade"],
        "per_share_risk": round(per_share_risk, 2),
        "executable": executable,
        "reasons": list(dict.fromkeys(reasons)),
        "formula": "向下取整[单笔风险预算÷(计划买入中值-止损价)÷100]×100，并受单股、总仓和可用现金上限约束",
    }


def build_holding_action(holding: dict, trend: dict, account: dict) -> dict:
    current = _number(trend.get("close"), _number(holding.get("current_price"), _number(holding.get("cost_price"))))
    cost = _number(holding.get("cost_price"))
    stop = _number(holding.get("stop_price"), _number(trend.get("stop_price")))
    quantity = max(0, int(_number(holding.get("quantity"))))
    pnl_pct = (current / cost - 1) * 100 if cost > 0 else None
    action = "持有"
    reason = "周度趋势保持，按结构止损和分批止盈管理"
    sell_quantity = 0
    if stop > 0 and current <= stop:
        action = "清仓"
        reason = "最新价格已触及或跌破结构止损"
        sell_quantity = quantity
    elif not trend.get("qualified") and cost > 0 and current >= cost * 0.98:
        action = "至少减仓50%"
        reason = "趋势失效后反弹至成本附近，成本价不构成继续持有理由"
        sell_quantity = math.ceil(quantity * 0.5 / 100) * 100
    elif not trend.get("qualified"):
        action = "减仓或退出"
        reason = "周度趋势或相对强度未确认，禁止通过加仓摊低成本"
        sell_quantity = math.ceil(quantity * 0.5 / 100) * 100
    elif cost > stop > 0 and current >= cost + (cost - stop) * 2.5:
        action = "止盈1/3并跟踪"
        reason = "达到约2.5R，兑现部分收益，剩余仓位按10日线或前低跟踪"
        sell_quantity = math.floor(quantity / 3 / 100) * 100
    elif cost > stop > 0 and current >= cost + (cost - stop) * 1.5:
        action = "止盈1/3"
        reason = "达到约1.5R，先锁定部分收益并评估抬高止损"
        sell_quantity = math.floor(quantity / 3 / 100) * 100
    sell_quantity = min(quantity, max(0, sell_quantity))
    t_eligible = bool(
        quantity >= 400
        and trend.get("qualified")
        and not account.get("current_week_frozen")
        and str(account.get("broker_conditional_orders")) == "已确认"
    )
    return {
        "code": holding.get("code"),
        "name": holding.get("name"),
        "quantity": quantity,
        "cost_price": round(cost, 2),
        "reference_price": round(current, 2),
        "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
        "stop_price": round(stop, 2) if stop else None,
        "action": action,
        "sell_quantity": sell_quantity,
        "reason": reason,
        "weekly_trend": trend,
        "t_eligible": t_eligible,
        "t_rule": "仅已有底仓可用20%-30%；不扩大总仓；周熔断、趋势破坏或条件单未确认时禁止做T",
    }


def score_weekly_candidate(item: dict) -> dict:
    trend = item.get("weekly_trend") or {}
    fundamental = _number(item.get("sector_adjusted_fundamental_score"), _number(item.get("fundamental_score")))
    board = _number(item.get("board_strength_score"), 0.0)
    primary_board = item.get("primary_board") or {}
    external = _number(primary_board.get("external_score"), 50.0)
    valuation = _number(item.get("valuation_score"), 50.0)
    extension = abs(_number(trend.get("extension_pct")))
    crowding = max(0.0, 100.0 - extension * 8)
    valuation_crowding = valuation * 0.6 + crowding * 0.4
    components = {
        "fundamental": round(fundamental, 2),
        "trend": round(_number(trend.get("trend_score")), 2),
        "board": round(board, 2),
        "external_event": round(external, 2),
        "valuation_crowding": round(valuation_crowding, 2),
    }
    score = round(sum(components[key] * weight for key, weight in WEEKLY_WEIGHTS.items()), 2)
    fundamental_risk = item.get("financial_risk") or {}
    eligible = bool(
        fundamental >= 60
        and board >= 55
        and trend.get("qualified")
        and not fundamental_risk.get("hard_block")
    )
    reasons = []
    if fundamental < 60:
        reasons.append("行业校准基本面不足60分")
    if board < 55:
        reasons.append("板块周度确认不足55分")
    if not trend.get("qualified"):
        reasons.extend(trend.get("reasons") or ["周度趋势未通过"])
    if fundamental_risk.get("hard_block"):
        reasons.extend(fundamental_risk.get("risks") or ["财务风险硬拦截"])
    return {"score": score, "components": components, "eligible": eligible, "reasons": list(dict.fromkeys(reasons))}


def build_event_scenarios(external: dict) -> list[dict]:
    markets = external.get("markets") or []
    events = external.get("events") or []
    positive = [row for row in markets if _number(row.get("change_pct")) > 0]
    negative = [row for row in markets if _number(row.get("change_pct")) < 0]
    event_names = [str(row.get("name") or row.get("event")) for row in events if row.get("name") or row.get("event")]
    return [
        {
            "name": "基准情景",
            "summary": "外部变化只作情景输入，本周名单仍需A股板块趋势、扩散度和个股相对强度确认。",
            "triggers": ["A股相关板块周趋势保持", "候选股未触发禁追或结构止损"],
        },
        {
            "name": "利好情景",
            "summary": ("相对偏强市场：" + "、".join(str(row.get("name")) for row in positive[:3])) if positive else "暂未取得明确的外盘利好共振证据。",
            "triggers": ["外部利好延续", "A股板块资金和上涨扩散同步确认"],
        },
        {
            "name": "利空情景",
            "summary": ("相对偏弱市场：" + "、".join(str(row.get("name")) for row in negative[:3])) if negative else ("需跟踪事件：" + "、".join(event_names[:3]) if event_names else "外部事件数据不足，按不确定情景控制仓位。"),
            "triggers": ["外部风险扩大", "A股相关板块破位或候选股相对强度转负"],
        },
    ]


def build_weekly_plan(
    candidates: list[dict],
    account: dict,
    external: dict,
    now: datetime,
    existing: dict | None = None,
    holding_actions: list[dict] | None = None,
) -> dict:
    identity = week_identity(now)
    existing = existing or {}
    current_by_code = {str(item.get("code") or "").zfill(6): item for item in candidates}
    frozen = existing.get("plan_id") == identity["plan_id"] and existing.get("selections") is not None
    if frozen:
        selection_specs = [
            (str(row.get("code") or "").zfill(6), str(row.get("role") or "主选"))
            for row in existing.get("selections") or []
        ]
    else:
        eligible = [item for item in candidates if (item.get("weekly_evaluation") or {}).get("eligible")]
        eligible.sort(key=lambda item: _number((item.get("weekly_evaluation") or {}).get("score")), reverse=True)
        selected = []
        industries = set()
        for item in eligible:
            industry = str(item.get("selection_industry") or item.get("industry") or item.get("code"))
            if len(selected) < 2 and industry in industries:
                continue
            selected.append(item)
            industries.add(industry)
            if len(selected) == 3:
                break
        if len(selected) < 3:
            for item in eligible:
                if item not in selected:
                    selected.append(item)
                    if len(selected) == 3:
                        break
        selection_specs = [(str(item.get("code") or "").zfill(6), "主选" if index < 2 else "备选") for index, item in enumerate(selected)]

    selections = []
    reserved = 0.0
    old_by_code = {str(row.get("code") or "").zfill(6): row for row in existing.get("selections") or []}
    for code, role in selection_specs:
        item = current_by_code.get(code)
        if not item:
            old = old_by_code.get(code, {})
            selections.append({**old, "code": code, "role": role, "status": "撤销", "withdraw_reason": "本周更新中已无法取得候选数据或硬性资格失效"})
            continue
        trend = item.get("weekly_trend") or {}
        position = position_plan(trend, account, role, reserved)
        if role == "主选":
            reserved += position["estimated_value"]
        evaluation = item.get("weekly_evaluation") or {}
        status = "可执行" if position["executable"] else "等待/不交易"
        if not evaluation.get("eligible"):
            status = "撤销"
        selections.append({
            "code": code,
            "name": item.get("name"),
            "industry": item.get("selection_industry") or item.get("industry"),
            "role": role,
            "status": status,
            "weekly_score": evaluation.get("score"),
            "score_components": evaluation.get("components"),
            "primary_board": item.get("primary_board"),
            "fundamental_score": item.get("sector_adjusted_fundamental_score") or item.get("fundamental_score"),
            "fundamental_evidence": item.get("fundamental_evidence"),
            "financial_risk": item.get("financial_risk"),
            "weekly_trend": trend,
            "position_plan": position,
            "invalidation": list(dict.fromkeys((evaluation.get("reasons") or []) + [
                "跌破结构止损",
                "板块趋势或相对强度转弱",
                "重大公告或国际事件破坏核心逻辑",
            ])),
            "t_rule": "默认关闭；仅已有底仓可用20%-30%做T，不扩大总仓，不在周熔断后操作",
        })

    active = [row for row in selections if row.get("status") != "撤销"]
    return {
        **identity,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "frozen": frozen,
        "state": "空仓" if not active else "冻结执行" if frozen else "待冻结",
        "selection_policy": "每周最多2只主选、1只备选；周内只允许撤销，不因排行变化新增股票",
        "account": account,
        "event_scenarios": build_event_scenarios(external),
        "holding_actions": holding_actions or [],
        "weights": WEEKLY_WEIGHTS,
        "selections": selections,
        "active_count": len(active),
        "execution_note": "研究池和周度候选均不是自动买入指令；股数是单股目标上限，不代表在未知现有仓位下追加买入。只有账户级亏损、趋势、价格和事件闸门同时通过才可执行。",
    }


def load_weekly_plan(path: Path = WEEKLY_PLAN_FILE) -> dict:
    return _read_json(path)


def save_weekly_plan(plan: dict, path: Path = WEEKLY_PLAN_FILE) -> dict:
    frozen = dict(plan)
    account = frozen.get("account") or {}
    frozen["account"] = {
        "equity": account.get("equity"),
        "last_week_return_pct": account.get("last_week_return_pct"),
        "current_week_return_pct": account.get("current_week_return_pct"),
        "holdings_tracking_enabled": False,
        "risk_profile": account.get("risk_profile"),
        "can_open_new": account.get("can_open_new"),
        "block_reasons": account.get("block_reasons"),
    }
    frozen["holding_actions"] = []
    frozen["frozen"] = True
    frozen["state"] = "空仓" if not frozen.get("selections") else "冻结执行"
    write_json(path, frozen)
    return frozen
