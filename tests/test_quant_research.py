import tempfile
import unittest
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from quant_backtest import BacktestCosts, run_factor_backtest
from quant_data import QuantDailyData, QuantDataConfig
from quant_factors import FactorParams, calculate_factors
from quant_optimizer import OptimizationConfig, walk_forward_optimize
from quant_pipeline import _save_outputs


def synthetic_prices(stock_count=8, days=180):
    dates = pd.bdate_range("2025-01-02", periods=days)
    rows = []
    for stock_index in range(stock_count):
        code = f"{stock_index + 1:06d}"
        trend = 0.0003 + stock_index * 0.00015
        close = 10 + stock_index
        for day_index, day in enumerate(dates):
            daily_return = trend + np.sin(day_index / 9 + stock_index) * 0.002
            open_price = close
            close = close * (1 + daily_return)
            rows.append({
                "date": day, "code": code, "name": f"股票{stock_index + 1}",
                "open": open_price, "high": max(open_price, close) * 1.01,
                "low": min(open_price, close) * 0.99, "close": close,
                "volume": 1_000_000 * (1 + stock_index / 10 + day_index / 1000),
                "amount": close * 1_000_000,
            })
    return pd.DataFrame(rows)


SMALL_PARAMS = FactorParams(
    momentum_window=5,
    trend_window=5,
    volatility_window=5,
    volume_window=5,
    rsi_window=5,
    bollinger_window=5,
    atr_window=5,
)


class QuantResearchTest(unittest.TestCase):
    def test_first_real_data_batch_keeps_datetime_for_cache_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            loader = QuantDailyData.__new__(QuantDailyData)
            loader.config = QuantDataConfig(cache_dir=directory)
            loader.cache_dir = Path(directory)
            loader.provider_name = "akshare"
            loader._fetch_akshare = lambda code, name, start, end: pd.DataFrame([{
                "date": "2026-08-14", "code": code, "name": name,
                "open": 10, "high": 11, "low": 9, "close": 10.5,
                "volume": 1000, "amount": 10500,
            }])

            result = loader._fetch_symbol(
                "000001", "平安银行", date(2026, 8, 1), date(2026, 8, 16)
            )

            self.assertTrue(pd.api.types.is_datetime64_any_dtype(result["date"]))
            self.assertEqual(len(result), 1)

    def test_factor_frame_contains_requested_factors_without_future_leakage(self):
        prices = synthetic_prices(stock_count=3, days=40)
        original = calculate_factors(prices, SMALL_PARAMS)
        cutoff = prices["date"].sort_values().unique()[25]

        changed = prices.copy()
        changed.loc[changed["date"] > cutoff, "close"] *= 10
        revised = calculate_factors(changed, SMALL_PARAMS)

        columns = {
            "momentum", "trend", "volatility", "volume_ratio", "rsi",
            "bollinger_position", "atr", "atr_pct",
        }
        self.assertTrue(columns.issubset(original.columns))
        left = original[original["date"] <= cutoff].reset_index(drop=True)
        right = revised[revised["date"] <= cutoff].reset_index(drop=True)
        pd.testing.assert_series_equal(left["momentum"], right["momentum"])

    def test_vectorized_backtest_selects_top_stocks_and_charges_costs(self):
        factors = calculate_factors(synthetic_prices(), SMALL_PARAMS)
        result = run_factor_backtest(
            factors,
            top_n=3,
            costs=BacktestCosts(commission=0.0003, stamp_tax=0.001, slippage=0.001),
        )

        self.assertEqual(len(result["signals"]), 3)
        self.assertGreater(result["equity"]["trading_cost"].sum(), 0)
        self.assertIn("annual_return", result["metrics"])
        self.assertIn("max_drawdown", result["metrics"])
        self.assertIn("sharpe_ratio", result["metrics"])

    def test_walk_forward_returns_out_of_sample_metrics_and_best_params(self):
        prices = synthetic_prices(days=150)
        config = OptimizationConfig(
            train_days=60,
            validation_days=20,
            top_n=3,
            momentum_windows=(5, 10),
            trend_windows=(5,),
            volatility_windows=(5,),
            volume_windows=(5,),
            rsi_windows=(5,),
            bollinger_windows=(5,),
            atr_windows=(5,),
        )

        result = walk_forward_optimize(prices, config=config)

        self.assertGreaterEqual(len(result["folds"]), 1)
        self.assertEqual(result["grid_size"], 2)
        self.assertIn(result["best_params"]["momentum_window"], (5, 10))
        self.assertIn("sharpe_ratio", result["oos_metrics"])
        self.assertFalse(result["oos_equity"].empty)

    def test_report_outputs_are_saved(self):
        prices = synthetic_prices(days=100)
        config = OptimizationConfig(
            train_days=60, validation_days=20, top_n=3,
            momentum_windows=(5,), trend_windows=(5,), volatility_windows=(5,), volume_windows=(5,),
            rsi_windows=(5,), bollinger_windows=(5,), atr_windows=(5,),
        )
        result = walk_forward_optimize(prices, config=config)
        metadata = {
            "generated_at": "2026-08-16 16:30:00", "signal_date": "2026-08-16",
            "provider": "synthetic", "stock_count": 8, "trading_days": 100,
            "universe_limit": 8,
        }
        with tempfile.TemporaryDirectory() as directory:
            files = _save_outputs(result, metadata, Path(directory))
            for path in files.values():
                self.assertTrue(Path(path).exists())
            report = Path(files["report"]).read_text(encoding="utf-8")
            self.assertIn("样本外表现", report)
            self.assertIn("不代表提高了交易结果的确定性", report)

if __name__ == "__main__":
    unittest.main()
