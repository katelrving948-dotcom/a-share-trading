import tempfile
import unittest
import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from quant_backtest import BacktestCosts, run_factor_backtest
from quant_data import QuantDailyData, QuantDataConfig
from quant_factors import FactorParams, calculate_factors
from quant_journal import build_optimization_entry, validate_previous_signals
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
    def test_previous_close_signals_are_validated_on_next_session(self):
        prices = synthetic_prices(stock_count=3, days=20)
        dates = sorted(prices["date"].unique())
        signal_date = pd.Timestamp(dates[-2])
        signals = prices[prices["date"] == signal_date][
            ["date", "code", "name", "close"]
        ].copy()
        signals["factor_score"] = [0.9, 0.8, 0.7]

        validation = validate_previous_signals(signals, prices)

        self.assertEqual(validation["status"], "validated")
        self.assertEqual(validation["signal_date"], signal_date.strftime("%Y-%m-%d"))
        self.assertEqual(validation["validated_count"], 3)
        self.assertIn("excess_return", validation)

    def test_optimization_log_states_changed_parameters_and_guardrail(self):
        entry = build_optimization_entry(
            "2026-08-16 16:30:00",
            {"best_params": {"momentum_window": 20}, "oos_metrics": {"sharpe_ratio": 0.8}},
            {"best_params": {"momentum_window": 60}, "oos_metrics": {"sharpe_ratio": 1.0}, "grid_size": 9},
            {"status": "validated", "signal_date": "2026-08-14", "validation_date": "2026-08-15"},
        )

        self.assertEqual(entry["parameter_changes"][0]["part"], "momentum_window")
        self.assertIn("预设参数网格", entry["guardrail"])

    def test_tencent_history_source_is_normalized_to_ohlcv(self):
        loader = QuantDailyData.__new__(QuantDailyData)
        loader.config = QuantDataConfig()
        loader._module = type("TencentAkshare", (), {
            "stock_zh_a_hist_tx": staticmethod(lambda **kwargs: pd.DataFrame([{
                "date": "2026-08-14", "open": 10, "high": 11, "low": 9,
                "close": 10.5, "volume": 1000, "amount": 10500, "turnover": 0.01,
            }]))
        })()

        with patch.dict("os.environ", {"AKSHARE_HISTORY_SOURCE": "tx"}):
            result = loader._fetch_akshare(
                "000001", "平安银行", date(2026, 8, 1), date(2026, 8, 14)
            )

        self.assertEqual(result.iloc[0]["code"], "000001")
        self.assertEqual(result.iloc[0]["name"], "平安银行")
        self.assertEqual(result.iloc[0]["close"], 10.5)

    def test_akshare_universe_falls_back_to_fundamental_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "fundamental.json"
            snapshot.write_text(json.dumps({"rows": [
                {"code": "000001", "name": "平安银行"},
                {"code": "600000", "name": "浦发银行"},
            ]}, ensure_ascii=False), encoding="utf-8")
            loader = QuantDailyData.__new__(QuantDailyData)
            loader.config = QuantDataConfig(universe_limit=2)
            loader.provider_name = "akshare"
            loader._module = type("BrokenAkshare", (), {
                "stock_zh_a_spot_em": staticmethod(lambda: (_ for _ in ()).throw(ConnectionError("offline")))
            })()

            with patch.dict("os.environ", {"QUANT_UNIVERSE_SNAPSHOT": str(snapshot)}):
                result = loader.list_universe()

            self.assertEqual(result["code"].tolist(), ["000001", "600000"])

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
            saved_factors = pd.read_csv(files["factors"])
            self.assertIn("factor_score", saved_factors.columns)

if __name__ == "__main__":
    unittest.main()
