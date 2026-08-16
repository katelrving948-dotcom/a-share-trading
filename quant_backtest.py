"""Vectorized daily-rebalanced cross-sectional factor backtest."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from quant_factors import add_cross_sectional_score


@dataclass(frozen=True)
class BacktestCosts:
    commission: float = 0.0003
    stamp_tax: float = 0.001
    slippage: float = 0.001

    def to_dict(self) -> dict:
        return asdict(self)


def _performance(net_returns: pd.Series) -> dict:
    returns = net_returns.dropna().astype(float)
    if returns.empty:
        return {
            "total_return": 0.0, "annual_return": 0.0,
            "max_drawdown": 0.0, "sharpe_ratio": 0.0,
            "trading_days": 0,
        }
    equity = (1 + returns).cumprod()
    years = len(returns) / 252
    annual = equity.iloc[-1] ** (1 / years) - 1 if years > 0 else 0
    drawdown = equity / equity.cummax() - 1
    volatility = returns.std(ddof=0)
    sharpe = returns.mean() / volatility * np.sqrt(252) if volatility > 0 else 0
    return {
        "total_return": round((equity.iloc[-1] - 1) * 100, 4),
        "annual_return": round(annual * 100, 4),
        "max_drawdown": round(drawdown.min() * 100, 4),
        "sharpe_ratio": round(float(sharpe), 4),
        "trading_days": int(len(returns)),
    }


def run_factor_backtest(
    factors: pd.DataFrame,
    top_n: int = 20,
    costs: BacktestCosts | None = None,
    start_date=None,
    end_date=None,
) -> dict:
    """Rank at close on day T, then apply those weights to day T+1 returns."""
    if top_n <= 0:
        raise ValueError("top_n 必须大于0")
    costs = costs or BacktestCosts()
    scored = add_cross_sectional_score(factors)
    if start_date is not None:
        scored = scored[scored["date"] >= pd.Timestamp(start_date)]
    if end_date is not None:
        scored = scored[scored["date"] <= pd.Timestamp(end_date)]
    if scored.empty:
        return {"metrics": _performance(pd.Series(dtype=float)), "equity": pd.DataFrame(), "signals": pd.DataFrame()}

    scored = scored.sort_values(["date", "factor_score"], ascending=[True, False])
    scored["selected"] = scored.groupby("date").cumcount() < top_n
    selected = scored[scored["selected"]].copy()
    counts = selected.groupby("date")["code"].transform("count")
    selected["target_weight"] = 1 / counts

    returns = scored.pivot(index="date", columns="code", values="return_1d").sort_index()
    weights = selected.pivot(index="date", columns="code", values="target_weight")
    weights = weights.reindex(index=returns.index, columns=returns.columns).fillna(0.0)
    held_weights = weights.shift(1).fillna(0.0)
    gross_return = (held_weights * returns.fillna(0.0)).sum(axis=1)

    weight_change = weights.diff().fillna(weights)
    buy_turnover = weight_change.clip(lower=0).sum(axis=1)
    sell_turnover = -weight_change.clip(upper=0).sum(axis=1)
    trading_cost = (
        buy_turnover * (costs.commission + costs.slippage)
        + sell_turnover * (costs.commission + costs.stamp_tax + costs.slippage)
    )
    net_return = gross_return - trading_cost
    equity = pd.DataFrame({
        "date": returns.index,
        "gross_return": gross_return.values,
        "trading_cost": trading_cost.values,
        "net_return": net_return.values,
        "turnover": (buy_turnover + sell_turnover).values,
    })
    equity["net_value"] = (1 + equity["net_return"]).cumprod()
    equity["drawdown"] = equity["net_value"] / equity["net_value"].cummax() - 1

    latest_date = selected["date"].max()
    signals = selected[selected["date"] == latest_date].copy()
    signals["rank"] = signals["factor_score"].rank(method="first", ascending=False).astype(int)
    signal_columns = [
        "date", "rank", "code", "name", "close", "factor_score",
        "momentum", "trend", "volatility", "volume_ratio", "rsi",
        "bollinger_position", "atr", "atr_pct", "target_weight",
    ]
    signals = signals[[column for column in signal_columns if column in signals.columns]]
    signals = signals.sort_values("rank").reset_index(drop=True)
    metrics = _performance(pd.Series(equity["net_return"].values, index=equity["date"]))
    metrics.update({
        "top_n": top_n,
        "average_daily_turnover": round(float(equity["turnover"].mean()), 4),
        "total_trading_cost": round(float(equity["trading_cost"].sum()) * 100, 4),
    })
    return {
        "metrics": metrics,
        "equity": equity.reset_index(drop=True),
        "signals": signals,
        "costs": costs.to_dict(),
    }
