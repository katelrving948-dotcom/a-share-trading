"""Persist next-day signal validation and a bounded daily optimization journal."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def update_selection_history(history: dict, snapshot: dict, prices: pd.DataFrame, limit: int = 250) -> dict:
    """Append a point-in-time noon snapshot and validate it on the next session."""
    snapshots = list(history.get("snapshots") or [])
    if snapshot.get("signal_date") and snapshot.get("rows"):
        snapshots = [
            item for item in snapshots
            if item.get("signal_date") != snapshot.get("signal_date")
        ]
        snapshots.append(snapshot)

    price_frame = prices[["date", "code", "close"]].copy()
    price_frame["date"] = pd.to_datetime(price_frame["date"], errors="coerce").dt.normalize()
    price_frame["code"] = price_frame["code"].astype(str).str.zfill(6)
    price_frame["close"] = pd.to_numeric(price_frame["close"], errors="coerce")
    price_frame = price_frame.dropna(subset=["date", "code", "close"])
    dates = sorted(price_frame["date"].unique())
    for item in snapshots:
        if item.get("status") == "validated":
            continue
        signal_date = pd.to_datetime(item.get("signal_date"), errors="coerce")
        if pd.isna(signal_date):
            item["status"] = "invalid"
            continue
        signal_date = signal_date.normalize()
        later_dates = [pd.Timestamp(value) for value in dates if pd.Timestamp(value) > signal_date]
        if not later_dates:
            item["status"] = "pending"
            continue
        validation_date = later_dates[0]
        signal_close = price_frame[price_frame["date"] == signal_date].set_index("code")["close"]
        validation_close = price_frame[price_frame["date"] == validation_date].set_index("code")["close"]
        validated_rows = []
        for row in item.get("rows") or []:
            code = str(row.get("code", "")).zfill(6)
            if code not in signal_close or code not in validation_close or signal_close[code] <= 0:
                continue
            validated_rows.append({
                **row,
                "return_pct": round((float(validation_close[code]) / float(signal_close[code]) - 1) * 100, 4),
            })
        item["rows"] = validated_rows
        item["validation_date"] = validation_date.strftime("%Y-%m-%d")
        item["status"] = "validated" if validated_rows else "missing"
    snapshots.sort(key=lambda item: str(item.get("signal_date") or ""))
    return {"updated_at": pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d %H:%M:%S"), "snapshots": snapshots[-limit:]}


def validate_previous_signals(signals: pd.DataFrame, prices: pd.DataFrame) -> dict:
    """Validate the previous published close signal on its next available session."""
    if signals is None or signals.empty:
        return {"status": "missing", "message": "没有上一期量化信号可验证"}
    if prices is None or prices.empty:
        return {"status": "missing", "message": "没有价格数据可完成次日验证"}

    signal_frame = signals.copy()
    signal_frame["date"] = pd.to_datetime(signal_frame["date"], errors="coerce")
    signal_frame["code"] = signal_frame["code"].astype(str).str.zfill(6)
    signal_frame = signal_frame.dropna(subset=["date", "code"])
    if signal_frame.empty:
        return {"status": "missing", "message": "上一期信号缺少有效日期或代码"}

    signal_date = signal_frame["date"].max().normalize()
    signal_frame = signal_frame[signal_frame["date"].dt.normalize() == signal_date].copy()
    price_frame = prices[["date", "code", "close"]].copy()
    price_frame["date"] = pd.to_datetime(price_frame["date"], errors="coerce")
    price_frame["code"] = price_frame["code"].astype(str).str.zfill(6)
    price_frame["close"] = pd.to_numeric(price_frame["close"], errors="coerce")
    price_frame = price_frame.dropna(subset=["date", "code", "close"])
    later_dates = sorted(price_frame.loc[price_frame["date"] > signal_date, "date"].unique())
    if not later_dates:
        return {
            "status": "pending",
            "signal_date": signal_date.strftime("%Y-%m-%d"),
            "message": "尚无下一交易日收盘数据，等待次日验证",
        }

    validation_date = pd.Timestamp(later_dates[0]).normalize()
    signal_close = signal_frame[["code"]].copy()
    if "close" in signal_frame:
        signal_close["signal_close"] = pd.to_numeric(signal_frame["close"], errors="coerce")
    else:
        signal_close["signal_close"] = pd.NA
    historical_close = price_frame[price_frame["date"].dt.normalize() == signal_date][
        ["code", "close"]
    ].rename(columns={"close": "historical_close"})
    signal_close = signal_close.merge(historical_close, on="code", how="left")
    signal_close["signal_close"] = signal_close["signal_close"].fillna(
        signal_close["historical_close"]
    )
    next_close = price_frame[price_frame["date"].dt.normalize() == validation_date][
        ["code", "close"]
    ].rename(columns={"close": "validation_close"})
    checked = signal_close.merge(next_close, on="code", how="inner")
    checked = checked[checked["signal_close"] > 0].copy()
    if checked.empty:
        return {
            "status": "missing",
            "signal_date": signal_date.strftime("%Y-%m-%d"),
            "validation_date": validation_date.strftime("%Y-%m-%d"),
            "message": "下一交易日没有可匹配的信号价格",
        }

    checked["return_pct"] = (
        checked["validation_close"] / checked["signal_close"] - 1
    ) * 100
    previous_market = price_frame[price_frame["date"].dt.normalize() == signal_date][
        ["code", "close"]
    ].rename(columns={"close": "previous_close"})
    next_market = price_frame[price_frame["date"].dt.normalize() == validation_date][
        ["code", "close"]
    ].rename(columns={"close": "current_close"})
    market = previous_market.merge(next_market, on="code", how="inner")
    market = market[market["previous_close"] > 0].copy()
    benchmark_return = (
        ((market["current_close"] / market["previous_close"] - 1) * 100).mean()
        if not market.empty else 0.0
    )
    average_return = float(checked["return_pct"].mean())
    details = [
        {
            "code": str(row.code),
            "return_pct": round(float(row.return_pct), 3),
        }
        for row in checked.sort_values("return_pct", ascending=False).itertuples(index=False)
    ]
    return {
        "status": "validated",
        "signal_date": signal_date.strftime("%Y-%m-%d"),
        "validation_date": validation_date.strftime("%Y-%m-%d"),
        "signal_count": int(len(signal_frame)),
        "validated_count": int(len(checked)),
        "positive_count": int((checked["return_pct"] > 0).sum()),
        "hit_rate": round(float((checked["return_pct"] > 0).mean()) * 100, 2),
        "average_return": round(average_return, 3),
        "median_return": round(float(checked["return_pct"].median()), 3),
        "benchmark_return": round(float(benchmark_return), 3),
        "excess_return": round(average_return - float(benchmark_return), 3),
        "details": details,
        "message": "已按T日收盘信号核验下一交易日收盘表现",
    }


def build_optimization_entry(
    generated_at: str,
    previous_summary: dict,
    result: dict,
    validation: dict,
) -> dict:
    """Describe exactly what today's bounded optimizer changed or retained."""
    previous_params = previous_summary.get("best_params") or {}
    current_params = result.get("best_params") or {}
    changes = []
    for key in sorted(set(previous_params) | set(current_params)):
        before, after = previous_params.get(key), current_params.get(key)
        if isinstance(before, dict) or isinstance(after, dict):
            before_map, after_map = before or {}, after or {}
            for child in sorted(set(before_map) | set(after_map)):
                if before_map.get(child) != after_map.get(child):
                    changes.append({"part": f"{key}.{child}", "before": before_map.get(child), "after": after_map.get(child)})
        elif before != after:
            changes.append({"part": key, "before": before, "after": after})

    previous_metrics = previous_summary.get("oos_metrics") or {}
    current_metrics = result.get("oos_metrics") or {}
    metric_changes = []
    for key in ("annual_return", "max_drawdown", "sharpe_ratio"):
        before, after = previous_metrics.get(key), current_metrics.get(key)
        if before is not None and after is not None:
            metric_changes.append({
                "metric": key,
                "before": before,
                "after": after,
                "delta": round(float(after) - float(before), 4),
            })

    actions = [
        f"重新运行{result.get('grid_size', 0)}组参数的滚动训练与样本外回测",
        (
            f"完成{validation.get('signal_date')}信号在{validation.get('validation_date')}的次日验证"
            if validation.get("status") == "validated"
            else validation.get("message", "次日验证尚不可用")
        ),
    ]
    actions.append(
        f"调整{len(changes)}项参数" if changes else "参数保持不变，避免根据单日结果追涨杀跌式调参"
    )
    selection = result.get("selection_optimization") or {}
    if selection:
        actions.append(selection.get("message", "选股权重优化状态不可用"))
    return {
        "generated_at": generated_at,
        "validation": validation,
        "parameter_changes": changes,
        "metric_changes": metric_changes,
        "actions": actions,
        "selection_optimization": selection,
        "guardrail": "技术因子只在预设参数网格内按滚动样本外结果选择；选股权重仅使用逐日留存的时点快照，样本不足时保持默认值，单日验证不直接调参。",
    }


def append_optimization_log(path: Path, entry: dict, limit: int = 60) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        payload = {}
    entries = [
        item for item in payload.get("entries", [])
        if item.get("generated_at") != entry.get("generated_at")
    ]
    entries.append(entry)
    payload = {"updated_at": entry.get("generated_at"), "entries": entries[-limit:]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
