import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from fundamental import FundamentalScorer
from research_core import _morning_fund_score, build_morning_entry_plan, build_trade_decision, quant_model_gate, save_selection_snapshot, score_intersection


class ResearchCoreTest(unittest.TestCase):
    def test_selection_snapshot_keeps_point_in_time_components(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "selection.json"
            snapshot = save_selection_snapshot({
                "generated_at": "2026-08-17 12:00:00",
                "rules": {"selection_weights": {"fundamental": 0.4, "technical": 0.4, "board": 0.1, "morning_fund": 0.1}},
                "observations": [{"code": "000001", "rank": 1, "selection_score": 75, "selection_components": {"fundamental": 80, "technical": 70, "board": 75, "morning_fund": 65}}],
            }, path)

            self.assertEqual(snapshot["signal_date"], "2026-08-17")
            self.assertEqual(snapshot["rows"][0]["components"]["morning_fund"], 65)
            self.assertTrue(path.exists())

    def test_morning_fund_score_uses_main_net_ratio_with_neutral_fallback(self):
        self.assertEqual(_morning_fund_score({"available": True, "main_net_pct": 4}), 70)
        self.assertEqual(_morning_fund_score({"available": False}), 50)

    def test_morning_plan_uses_quant_atr_for_conditional_levels(self):
        plan = build_morning_entry_plan({
            "close_price": 10.2,
            "morning_session": {
                "completed": True, "status": "上午盘已完成",
                "open": 10, "high": 10.4, "low": 9.9, "close": 10.2, "vwap": 10.1,
                "change_pct": 2, "range_pct": 5, "up_minute_ratio": 0.6,
                "close_position": 0.6, "above_vwap_ratio": 0.6,
            },
        }, atr_pct=0.02)

        self.assertTrue(plan["levels_available"])
        self.assertEqual(plan["window"], "09:30-11:30")
        self.assertGreater(plan["entry_zone"]["low"], plan["stop_zone"]["high"])
        self.assertEqual(plan["quant_atr_pct"], 2.0)

    def test_trade_decision_requires_sector_quant_and_live_entry(self):
        decision = build_trade_decision({
            "board_strength_score": 70,
            "fundamental_score": 75,
            "technical_score": 72,
            "morning_plan": {
                "actionable": True,
                "status": "上午强势承接",
                "execution_state": "当前价进入回踩进场区",
            },
        })

        self.assertEqual(decision["status"], "可执行观察")
        self.assertTrue(decision["quant_gate"]["passed"])

    def test_stale_intraday_data_does_not_generate_today_levels(self):
        plan = build_morning_entry_plan({
            "trade_date": "20260815",
            "morning_session": {"completed": True},
        }, atr_pct=0.02)

        self.assertFalse(plan["levels_available"])
        self.assertEqual(plan["status"], "非当日分时数据")

    def test_fundamental_score_has_four_transparent_dimensions(self):
        row = FundamentalScorer._evaluate({
            "code": "1", "name": "样本", "available": True,
            "annualized_roe": 15, "roe": 15, "gross_margin": 30,
            "revenue_growth": 12, "profit_growth": 18,
            "eps": 1, "operating_cf_per_share": 1.2, "pe": 18, "pb": 2.5,
        })
        self.assertEqual(row["code"], "000001")
        self.assertEqual(
            set(("quality_score", "growth_score", "valuation_score", "cashflow_score"))
            - set(row),
            set(),
        )
        self.assertGreaterEqual(row["fundamental_score"], 0)
        self.assertLessEqual(row["fundamental_score"], 100)

    @patch.dict("os.environ", {"PUSH_FUNDAMENTAL_MIN": "60", "PUSH_TECHNICAL_MIN": "60", "PUSH_DISPLAY_LIMIT": "20"})
    def test_intersection_is_not_fixed_to_ten_and_requires_both_scores(self):
        fundamental = {"rows": [
            {"code": "000001", "name": "双达标", "fundamental_score": 75},
            {"code": "000002", "name": "技术不达标", "fundamental_score": 80},
            {"code": "000003", "name": "基本面不达标", "fundamental_score": 59},
        ]}
        technical = {"summary": {"oos_metrics": {
            "annual_return": 8, "max_drawdown": -12,
            "sharpe_ratio": 0.8, "trading_days": 252,
        }}, "rows": [
            {"code": "000001", "technical_score": 70},
            {"code": "000002", "technical_score": 50},
            {"code": "000003", "technical_score": 90},
        ]}
        result = score_intersection(fundamental, technical)
        self.assertEqual([row["code"] for row in result], ["000001"])
        self.assertEqual(result[0]["combined_score"], 72.5)

    def test_negative_oos_performance_keeps_candidate_visible_but_closes_entry_gate(self):
        technical = {"summary": {"oos_metrics": {
            "annual_return": -5.2, "max_drawdown": -35.1,
            "sharpe_ratio": -0.12, "trading_days": 378,
        }}, "rows": [{"code": "000001", "technical_score": 90}]}

        gate = quant_model_gate(technical)
        result = score_intersection(
            {"rows": [{"code": "000001", "fundamental_score": 90}]}, technical
        )

        self.assertFalse(gate["passed"])
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["quant_model_passed"])
        result[0].update({
            "board_strength_score": 80,
            "morning_plan": {
                "actionable": True,
                "status": "上午强势承接",
                "execution_state": "当前价进入回踩进场区",
            },
        })
        self.assertEqual(build_trade_decision(result[0])["status"], "不交易")

    @patch.dict("os.environ", {
        "PUSH_FUNDAMENTAL_MIN": "60", "PUSH_TECHNICAL_MIN": "60",
        "PUSH_FUNDAMENTAL_HARD_FLOOR": "50", "PUSH_TECHNICAL_HARD_FLOOR": "50",
        "PUSH_INDUSTRY_RELATIVE_WEIGHT": "0.30",
    })
    def test_industry_relative_score_admits_sector_leader_without_removing_hard_floor(self):
        fundamental = {"rows": [
            {"code": "000001", "industry": "科技", "fundamental_score": 55},
            {"code": "000002", "industry": "科技", "fundamental_score": 40},
        ]}
        technical = {"summary": {"oos_metrics": {
            "annual_return": 8, "max_drawdown": -12,
            "sharpe_ratio": 0.8, "trading_days": 252,
        }}, "rows": [
            {"code": "000001", "technical_score": 80},
            {"code": "000002", "technical_score": 70},
        ]}

        result = score_intersection(fundamental, technical)

        self.assertEqual([row["code"] for row in result], ["000001"])
        self.assertEqual(result[0]["sector_adjusted_fundamental_score"], 68.5)
        self.assertEqual(result[0]["industry_fundamental_percentile"], 100.0)

    def test_legacy_listing_board_is_not_treated_as_an_industry(self):
        fundamental = {"rows": [
            {"code": "000001", "industry": "上海主板", "fundamental_score": 70},
        ]}
        technical = {"summary": {"oos_metrics": {
            "annual_return": 8, "max_drawdown": -12,
            "sharpe_ratio": 0.8, "trading_days": 252,
        }}, "rows": [{"code": "000001", "technical_score": 70}]}

        result = score_intersection(fundamental, technical)

        self.assertEqual(result[0]["industry"], "")
        self.assertEqual(result[0]["listing_board"], "上海主板")
        self.assertEqual(result[0]["sector_adjusted_fundamental_score"], 70)


if __name__ == "__main__":
    unittest.main()
