"""Cross-sectional technical factors for the independent quant research lane."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FactorParams:
    momentum_window: int = 60
    trend_window: int = 20
    volatility_window: int = 20
    volume_window: int = 20
    rsi_window: int = 14
    bollinger_window: int = 20
    atr_window: int = 14

    def to_dict(self) -> dict:
        return asdict(self)


REQUIRED_COLUMNS = {"date", "code", "open", "high", "low", "close", "volume"}
FACTOR_REGISTRY = {
    "momentum": {"raw_column": "momentum", "score_column": "score_momentum", "mapping_type": "high", "label": "动量", "formula": "close / close[N] - 1", "mapping": "横截面百分位，越高越好", "window_key": "momentum_window"},
    "trend": {"raw_column": "trend", "score_column": "score_trend", "mapping_type": "high", "label": "趋势", "formula": "close / MA(N) - 1", "mapping": "横截面百分位，越高越好", "window_key": "trend_window"},
    "low_volatility": {"raw_column": "volatility", "score_column": "score_low_volatility", "mapping_type": "low", "label": "低波动", "formula": "std(日收益,N) × √252", "mapping": "1 - 横截面百分位，越低波越好", "window_key": "volatility_window"},
    "volume_ratio": {"raw_column": "volume_ratio", "score_column": "score_volume_ratio", "mapping_type": "high", "label": "量比", "formula": "volume / MA(volume,N)", "mapping": "横截面百分位，越高越好", "window_key": "volume_window"},
    "rsi": {"raw_column": "rsi", "score_column": "score_rsi", "mapping_type": "target", "target": 55, "label": "RSI健康度", "formula": "RSI(N)", "mapping": "按 -|RSI-55| 排名，越接近55越好", "window_key": "rsi_window"},
    "bollinger": {"raw_column": "bollinger_position", "score_column": "score_bollinger", "mapping_type": "high", "label": "布林位置", "formula": "(close-lower)/(upper-lower)", "mapping": "横截面百分位，越高越好", "window_key": "bollinger_window"},
    "low_atr": {"raw_column": "atr_pct", "score_column": "score_low_atr", "mapping_type": "low", "label": "低ATR", "formula": "ATR(N) / close", "mapping": "1 - 横截面百分位，越低越好", "window_key": "atr_window"},
}
SCORE_COLUMNS = {name: spec["score_column"] for name, spec in FACTOR_REGISTRY.items()}
DEFAULT_SCORE_WEIGHTS = {name: 1 / len(SCORE_COLUMNS) for name in SCORE_COLUMNS}


def _rsi(close: pd.Series, window: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window, min_periods=window).mean()
    loss = -delta.clip(upper=0).rolling(window, min_periods=window).mean()
    rs = gain / loss.replace(0, np.nan)
    result = 100 - 100 / (1 + rs)
    return result.where(loss.ne(0), 100.0).where(gain.ne(0), 0.0)


def _stock_factors(group: pd.DataFrame, params: FactorParams) -> pd.DataFrame:
    frame = group.sort_values("date").copy()
    close = frame["close"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    volume = frame["volume"].astype(float)
    returns = close.pct_change()

    frame["return_1d"] = returns
    frame["momentum"] = close.pct_change(params.momentum_window)
    moving_average = close.rolling(params.trend_window, min_periods=params.trend_window).mean()
    frame["trend"] = close / moving_average.replace(0, np.nan) - 1
    frame["volatility"] = (
        returns.rolling(params.volatility_window, min_periods=params.volatility_window).std()
        * np.sqrt(252)
    )
    frame["volume_ratio"] = volume / volume.rolling(
        params.volume_window, min_periods=params.volume_window
    ).mean().replace(0, np.nan)
    frame["rsi"] = _rsi(close, params.rsi_window)

    middle = close.rolling(
        params.bollinger_window, min_periods=params.bollinger_window
    ).mean()
    deviation = close.rolling(
        params.bollinger_window, min_periods=params.bollinger_window
    ).std()
    upper = middle + 2 * deviation
    lower = middle - 2 * deviation
    frame["bollinger_position"] = (close - lower) / (upper - lower).replace(0, np.nan)

    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    frame["atr"] = true_range.rolling(
        params.atr_window, min_periods=params.atr_window
    ).mean()
    frame["atr_pct"] = frame["atr"] / close.replace(0, np.nan)
    return frame


def calculate_factors(prices: pd.DataFrame, params: FactorParams | None = None) -> pd.DataFrame:
    """Return a long factor DataFrame without using future prices."""
    params = params or FactorParams()
    missing = REQUIRED_COLUMNS.difference(prices.columns)
    if missing:
        raise ValueError(f"因子数据缺少字段: {', '.join(sorted(missing))}")
    if prices.empty:
        return prices.copy()

    clean = prices.copy()
    clean["date"] = pd.to_datetime(clean["date"])
    clean["code"] = clean["code"].astype(str).str.zfill(6)
    numeric = ["open", "high", "low", "close", "volume"]
    for column in numeric:
        clean[column] = pd.to_numeric(clean[column], errors="coerce")
    clean = clean.dropna(subset=["date", "code", *numeric])
    clean = clean.drop_duplicates(["date", "code"], keep="last")

    result = pd.concat(
        (_stock_factors(group, params) for _, group in clean.groupby("code")),
        ignore_index=True,
    )
    return result.sort_values(["date", "code"]).reset_index(drop=True)


def add_cross_sectional_score(
    factors: pd.DataFrame,
    weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Build a transparent equal-weight score from the requested factor family."""
    frame = factors.copy()
    required = [spec["raw_column"] for spec in FACTOR_REGISTRY.values()]
    frame = frame.dropna(subset=required)
    if frame.empty:
        frame["factor_score"] = pd.Series(dtype=float)
        return frame

    grouped = frame.groupby("date")
    for spec in FACTOR_REGISTRY.values():
        raw_column = spec["raw_column"]
        if spec["mapping_type"] == "low":
            frame[spec["score_column"]] = 1 - grouped[raw_column].rank(pct=True)
        elif spec["mapping_type"] == "target":
            target_distance = -(frame[raw_column] - float(spec["target"])).abs()
            frame[spec["score_column"]] = target_distance.groupby(frame["date"]).rank(pct=True)
        else:
            frame[spec["score_column"]] = grouped[raw_column].rank(pct=True)
    raw_weights = weights or DEFAULT_SCORE_WEIGHTS
    selected_weights = {
        name: max(0.0, float(raw_weights.get(name, 0))) for name in SCORE_COLUMNS
    }
    total_weight = sum(selected_weights.values())
    if total_weight <= 0:
        raise ValueError("因子权重合计必须大于0")
    frame["factor_score"] = sum(
        frame[column] * (selected_weights[name] / total_weight)
        for name, column in SCORE_COLUMNS.items()
    )
    return frame
