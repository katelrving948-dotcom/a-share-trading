"""Bounded optimization for the outer stock-selection score weights."""

from __future__ import annotations

import math
import statistics
from itertools import product


DEFAULT_SELECTION_WEIGHTS = {
    "fundamental": 0.40,
    "technical": 0.40,
    "board": 0.10,
    "morning_fund": 0.10,
}
SELECTION_WEIGHT_OPTIONS = {
    "fundamental": (0.30, 0.35, 0.40, 0.45, 0.50),
    "technical": (0.30, 0.35, 0.40, 0.45, 0.50),
    "board": (0.05, 0.10, 0.15, 0.20),
    "morning_fund": (0.05, 0.10, 0.15, 0.20),
}


def normalize_selection_weights(weights: dict | None) -> dict[str, float]:
    raw = weights or DEFAULT_SELECTION_WEIGHTS
    selected = {
        name: max(0.0, float(raw.get(name, 0)))
        for name in DEFAULT_SELECTION_WEIGHTS
    }
    total = sum(selected.values())
    if total <= 0:
        return DEFAULT_SELECTION_WEIGHTS.copy()
    return {name: round(value / total, 6) for name, value in selected.items()}


def selection_weight_grid() -> list[dict[str, float]]:
    names = tuple(SELECTION_WEIGHT_OPTIONS)
    return [
        dict(zip(names, values))
        for values in product(*(SELECTION_WEIGHT_OPTIONS[name] for name in names))
        if math.isclose(sum(values), 1.0, abs_tol=1e-9)
    ]


def score_selection_components(components: dict, weights: dict | None = None) -> float:
    normalized = normalize_selection_weights(weights)
    return round(sum(float(components.get(name, 50)) * weight for name, weight in normalized.items()), 2)


def optimize_selection_weights(
    history: dict,
    min_days: int = 20,
    top_n: int = 5,
) -> dict:
    validated = [
        snapshot for snapshot in history.get("snapshots", [])
        if snapshot.get("status") == "validated" and snapshot.get("rows")
    ]
    if len(validated) < min_days:
        return {
            "status": "accumulating",
            "weights": DEFAULT_SELECTION_WEIGHTS.copy(),
            "validated_days": len(validated),
            "minimum_days": min_days,
            "grid_size": 0,
            "message": f"已积累{len(validated)}个验证日，满{min_days}日后启用选股权重优化",
        }

    trials = []
    for weights in selection_weight_grid():
        daily_excess = []
        for snapshot in validated:
            rows = [row for row in snapshot["rows"] if row.get("return_pct") is not None]
            if not rows:
                continue
            ranked = sorted(
                rows,
                key=lambda row: score_selection_components(row.get("components") or {}, weights),
                reverse=True,
            )
            selected = ranked[:max(1, min(top_n, len(ranked)))]
            selected_return = statistics.fmean(float(row["return_pct"]) for row in selected)
            benchmark_return = statistics.fmean(float(row["return_pct"]) for row in rows)
            daily_excess.append(selected_return - benchmark_return)
        if not daily_excess:
            continue
        average_excess = statistics.fmean(daily_excess)
        deviation = statistics.pstdev(daily_excess)
        sharpe = average_excess / deviation * math.sqrt(252) if deviation > 0 else 0.0
        trials.append({
            "weights": weights,
            "sharpe_ratio": round(sharpe, 4),
            "average_excess_return": round(average_excess, 4),
            "positive_day_rate": round(sum(value > 0 for value in daily_excess) / len(daily_excess) * 100, 2),
            "evaluated_days": len(daily_excess),
        })
    if not trials:
        return {
            "status": "insufficient",
            "weights": DEFAULT_SELECTION_WEIGHTS.copy(),
            "validated_days": len(validated),
            "minimum_days": min_days,
            "grid_size": 0,
            "message": "历史快照缺少可验证收益，继续使用默认选股权重",
        }
    best = max(trials, key=lambda trial: (trial["sharpe_ratio"], trial["average_excess_return"]))
    return {
        "status": "optimized",
        "weights": normalize_selection_weights(best["weights"]),
        "validated_days": len(validated),
        "minimum_days": min_days,
        "grid_size": len(trials),
        "metrics": {key: value for key, value in best.items() if key != "weights"},
        "message": "使用逐日留存的午间候选快照及下一交易日收益优化选股权重",
    }
