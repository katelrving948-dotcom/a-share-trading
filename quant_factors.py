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


def add_cross_sectional_score(factors: pd.DataFrame) -> pd.DataFrame:
    """Build a transparent equal-weight score from the requested factor family."""
    frame = factors.copy()
    required = [
        "momentum", "trend", "volatility", "volume_ratio", "rsi",
        "bollinger_position", "atr_pct",
    ]
    frame = frame.dropna(subset=required)
    if frame.empty:
        frame["factor_score"] = pd.Series(dtype=float)
        return frame

    grouped = frame.groupby("date")
    frame["score_momentum"] = grouped["momentum"].rank(pct=True)
    frame["score_trend"] = grouped["trend"].rank(pct=True)
    frame["score_low_volatility"] = 1 - grouped["volatility"].rank(pct=True)
    frame["score_volume_ratio"] = grouped["volume_ratio"].rank(pct=True)
    frame["rsi_health"] = -(frame["rsi"] - 55).abs()
    frame["score_rsi"] = frame.groupby("date")["rsi_health"].rank(pct=True)
    frame["score_bollinger"] = grouped["bollinger_position"].rank(pct=True)
    frame["score_low_atr"] = 1 - grouped["atr_pct"].rank(pct=True)
    score_columns = [
        "score_momentum", "score_trend", "score_low_volatility", "score_volume_ratio",
        "score_rsi", "score_bollinger", "score_low_atr",
    ]
    frame["factor_score"] = frame[score_columns].mean(axis=1)
    return frame.drop(columns="rsi_health")
