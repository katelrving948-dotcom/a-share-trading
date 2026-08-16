"""Walk-forward factor parameter optimization with out-of-sample reporting."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quant_backtest import BacktestCosts, run_factor_backtest
from quant_factors import FactorParams, calculate_factors


@dataclass(frozen=True)
class OptimizationConfig:
    train_days: int = 504
    validation_days: int = 126
    top_n: int = 20
    momentum_windows: tuple[int, ...] = (20, 60, 120)
    trend_windows: tuple[int, ...] = (20, 60, 120)
    volatility_windows: tuple[int, ...] = (20,)
    volume_windows: tuple[int, ...] = (20,)
    rsi_windows: tuple[int, ...] = (14,)
    bollinger_windows: tuple[int, ...] = (20,)
    atr_windows: tuple[int, ...] = (14,)

    def parameter_grid(self) -> list[FactorParams]:
        return [
            FactorParams(*values)
            for values in itertools.product(
                self.momentum_windows,
                self.trend_windows,
                self.volatility_windows,
                self.volume_windows,
                self.rsi_windows,
                self.bollinger_windows,
                self.atr_windows,
            )
        ]


def _aggregate_oos(equities: list[pd.DataFrame]) -> tuple[pd.DataFrame, dict]:
    if not equities:
        return pd.DataFrame(), {}
    frame = pd.concat(equities, ignore_index=True)
    frame = frame.drop_duplicates("date", keep="last").sort_values("date")
    frame["net_value"] = (1 + frame["net_return"]).cumprod()
    frame["drawdown"] = frame["net_value"] / frame["net_value"].cummax() - 1
    returns = frame["net_return"]
    years = len(returns) / 252
    annual = frame["net_value"].iloc[-1] ** (1 / years) - 1 if years > 0 else 0
    volatility = returns.std(ddof=0)
    sharpe = returns.mean() / volatility * np.sqrt(252) if volatility > 0 else 0
    metrics = {
        "total_return": round((frame["net_value"].iloc[-1] - 1) * 100, 4),
        "annual_return": round(annual * 100, 4),
        "max_drawdown": round(frame["drawdown"].min() * 100, 4),
        "sharpe_ratio": round(float(sharpe), 4),
        "trading_days": int(len(frame)),
    }
    return frame.reset_index(drop=True), metrics


def walk_forward_optimize(
    prices: pd.DataFrame,
    config: OptimizationConfig | None = None,
    costs: BacktestCosts | None = None,
) -> dict:
    """Optimize on trailing two years and evaluate on the following six months."""
    config = config or OptimizationConfig()
    costs = costs or BacktestCosts()
    dates = pd.Index(sorted(pd.to_datetime(prices["date"]).dropna().unique()))
    needed = config.train_days + config.validation_days
    if len(dates) < needed:
        raise ValueError(f"滚动优化至少需要{needed}个交易日，当前只有{len(dates)}个")

    folds = []
    oos_equities = []
    last_best_params = None
    grid = config.parameter_grid()
    for validation_start_index in range(
        config.train_days,
        len(dates) - config.validation_days + 1,
        config.validation_days,
    ):
        train_start = dates[validation_start_index - config.train_days]
        train_end = dates[validation_start_index - 1]
        validation_start = dates[validation_start_index]
        validation_end = dates[validation_start_index + config.validation_days - 1]

        trials = []
        best = None
        best_factors = None
        for params in grid:
            factors = calculate_factors(prices, params)
            train = run_factor_backtest(
                factors, top_n=config.top_n, costs=costs,
                start_date=train_start, end_date=train_end,
            )
            sharpe = float(train["metrics"].get("sharpe_ratio") or 0)
            trial = {
                "params": params.to_dict(),
                "train_sharpe": sharpe,
                "train_annual_return": train["metrics"].get("annual_return", 0),
                "train_max_drawdown": train["metrics"].get("max_drawdown", 0),
            }
            trials.append(trial)
            if best is None or sharpe > best["train_sharpe"]:
                best = trial
                best_factors = factors

        if best is None or best_factors is None:
            continue
        validation = run_factor_backtest(
            best_factors, top_n=config.top_n, costs=costs,
            start_date=validation_start, end_date=validation_end,
        )
        last_best_params = FactorParams(**best["params"])
        folds.append({
            "train_start": pd.Timestamp(train_start).strftime("%Y-%m-%d"),
            "train_end": pd.Timestamp(train_end).strftime("%Y-%m-%d"),
            "validation_start": pd.Timestamp(validation_start).strftime("%Y-%m-%d"),
            "validation_end": pd.Timestamp(validation_end).strftime("%Y-%m-%d"),
            "best_params": best["params"],
            "train_metrics": {
                "sharpe_ratio": best["train_sharpe"],
                "annual_return": best["train_annual_return"],
                "max_drawdown": best["train_max_drawdown"],
            },
            "validation_metrics": validation["metrics"],
            "trial_count": len(trials),
        })
        oos_equities.append(validation["equity"])

    if last_best_params is None:
        raise RuntimeError("滚动优化未形成有效折次")
    oos_equity, oos_metrics = _aggregate_oos(oos_equities)
    latest_factors = calculate_factors(prices, last_best_params)
    latest_backtest = run_factor_backtest(
        latest_factors, top_n=config.top_n, costs=costs,
    )
    return {
        "best_params": last_best_params.to_dict(),
        "folds": folds,
        "oos_metrics": oos_metrics,
        "oos_equity": oos_equity,
        "full_backtest": latest_backtest,
        "factors": latest_factors,
        "grid_size": len(grid),
    }
