import unittest
from unittest.mock import Mock

from data_feed import DataFeed
from fundamental import build_opening_entry_plan, build_trade_decision


class OpeningEntryPlanTest(unittest.TestCase):
    def test_current_tencent_minute_payload_builds_opening_window(self):
        response = Mock()
        response.__bool__ = Mock(return_value=True)
        response.json.return_value = {
            "code": 0,
            "msg": "",
            "data": {
                "sh600989": {
                    "data": {
                        "date": "20260810",
                        "data": [
                            "0930 23.60 100 236000.00",
                            "0935 23.70 180 425600.00",
                            "0940 23.75 260 615600.00",
                            "0945 23.80 340 806000.00",
                            "0950 23.85 420 996800.00",
                            "0955 23.90 500 1188000.00",
                            "0958 23.95 580 1380000.00",
                            "0959 24.00 660 1572000.00",
                            "1000 24.05 740 1764400.00",
                        ],
                    }
                }
            },
        }
        feed = DataFeed.__new__(DataFeed)
        feed._request = Mock(return_value=response)

        intraday = feed.get_intraday_minute("600989")

        self.assertTrue(intraday["available"])
        self.assertTrue(intraday["opening_30m"]["completed"])
        self.assertEqual(intraday["opening_30m"]["sample_count"], 9)
        self.assertAlmostEqual(intraday["opening_30m"]["vwap"], 23.84, places=2)
        feed._request.assert_called_once_with(
            "https://web.ifzq.gtimg.cn/appstock/app/minute/query",
            {"code": "sh600989"}, timeout=(3, 8), retries=2,
        )

    def test_completed_strong_window_builds_entry_and_stop_zones(self):
        rows = []
        times = ["0930", "0934", "0938", "0942", "0946", "0950", "0954", "0958", "1000"]
        prices = [10.00, 10.03, 10.02, 10.06, 10.08, 10.07, 10.11, 10.12, 10.15]
        for time_text, price in zip(times, prices):
            rows.append({
                "time": time_text,
                "price": price,
                "volume": 1000,
                "avg_price": 10.07,
            })

        opening = DataFeed._summarize_opening_window(rows)
        plan = build_opening_entry_plan({"opening_30m": opening})

        self.assertTrue(opening["completed"])
        self.assertTrue(plan["actionable"])
        self.assertEqual(plan["status"], "强势回踩")
        self.assertLess(plan["entry_zone"]["low"], plan["entry_zone"]["high"])
        self.assertLess(plan["stop_zone"]["high"], plan["entry_zone"]["low"])
        self.assertGreater(plan["breakout_trigger"], opening["high"])
        self.assertEqual(len(plan["take_profit_zones"]), 2)
        self.assertGreater(
            plan["take_profit_zones"][0]["low"], plan["entry_zone"]["high"]
        )
        self.assertGreater(
            plan["take_profit_zones"][1]["low"],
            plan["take_profit_zones"][0]["high"],
        )
        self.assertGreaterEqual(plan["take_profit_zones"][0]["risk_reward"], 1.5)
        self.assertGreaterEqual(plan["take_profit_zones"][1]["risk_reward"], 2.5)

    def test_price_above_chase_limit_keeps_levels_but_blocks_entry(self):
        opening = {
            "completed": True,
            "status": "首30分钟已完成",
            "open": 10.0,
            "high": 10.2,
            "low": 9.95,
            "close": 10.15,
            "vwap": 10.08,
            "change_pct": 1.5,
            "range_pct": 2.5,
            "up_minute_ratio": 0.7,
            "close_position": 0.8,
            "above_vwap_ratio": 0.7,
        }

        plan = build_opening_entry_plan({
            "opening_30m": opening,
            "close_price": 10.50,
        })

        self.assertFalse(plan["actionable"])
        self.assertTrue(plan["levels_available"])
        self.assertIn("暂不追涨", plan["status"])
        self.assertIsNotNone(plan["entry_zone"])
        self.assertEqual(len(plan["take_profit_zones"]), 2)

    def test_incomplete_window_waits_until_ten(self):
        opening = DataFeed._summarize_opening_window([
            {"time": "0930", "price": 10.0, "volume": 100, "avg_price": 10.0},
            {"time": "0945", "price": 10.1, "volume": 100, "avg_price": 10.05},
        ])
        plan = build_opening_entry_plan({"opening_30m": opening})

        self.assertFalse(opening["completed"])
        self.assertFalse(plan["actionable"])
        self.assertIn("等待10:00", plan["status"])

    def test_high_volatility_window_does_not_force_entry(self):
        opening = {
            "completed": True,
            "status": "首30分钟已完成",
            "open": 10.0,
            "high": 10.7,
            "low": 9.9,
            "close": 10.5,
            "vwap": 10.3,
            "change_pct": 5.0,
            "range_pct": 8.0,
            "up_minute_ratio": 0.7,
            "close_position": 0.75,
            "above_vwap_ratio": 0.7,
        }

        plan = build_opening_entry_plan({"opening_30m": opening})

        self.assertFalse(plan["actionable"])
        self.assertEqual(plan["status"], "暂不追涨")
        self.assertIsNone(plan["entry_zone"])

    def test_valid_plan_still_waits_until_entry_is_triggered(self):
        decision = build_trade_decision({
            "board_strength_score": 75,
            "composite_score": 72,
            "opening_plan": {
                "actionable": True,
                "levels_available": True,
                "status": "强势回踩",
                "execution_state": "等待回踩进场区或放量突破确认",
                "entry_zone": {"low": 10.0, "high": 10.1},
                "stop_zone": {"low": 9.7, "high": 9.8},
            },
        })

        self.assertEqual(decision["status"], "等待确认")
        self.assertFalse(decision["entry"]["passed"])
        self.assertEqual(decision["position_now"], 0)

    def test_three_passed_gates_build_bounded_reference_position(self):
        decision = build_trade_decision({
            "board_strength_score": 75,
            "composite_score": 72,
            "opening_plan": {
                "actionable": True,
                "levels_available": True,
                "status": "强势回踩",
                "execution_state": "当前价进入回踩进场区，可结合量能分批观察",
                "entry_zone": {"low": 10.0, "high": 10.1},
                "stop_zone": {"low": 9.7, "high": 9.8},
            },
        })

        self.assertEqual(decision["status"], "可执行观察")
        self.assertTrue(decision["entry"]["passed"])
        self.assertEqual(decision["allowed_loss"], 250)
        self.assertGreater(decision["position_cap"], 0)
        self.assertLessEqual(decision["position_cap"], 10000)
        self.assertEqual(decision["position_now"], decision["position_cap"])

    def test_missing_board_match_blocks_trade_even_with_entry_trigger(self):
        decision = build_trade_decision({
            "composite_score": 80,
            "opening_plan": {
                "actionable": True,
                "levels_available": True,
                "status": "强势回踩",
                "execution_state": "当前价进入回踩进场区，可结合量能分批观察",
            },
        })

        self.assertEqual(decision["status"], "不交易")
        self.assertFalse(decision["board"]["passed"])

    def test_position_limit_blocks_stock_when_one_lot_exceeds_budget(self):
        decision = build_trade_decision({
            "board_strength_score": 80,
            "composite_score": 80,
            "opening_plan": {
                "actionable": True,
                "levels_available": True,
                "status": "强势回踩",
                "execution_state": "当前价进入回踩进场区，可结合量能分批观察",
                "entry_zone": {"low": 150.0, "high": 151.0},
                "stop_zone": {"low": 145.0, "high": 146.0},
            },
        })

        self.assertEqual(decision["status"], "不交易")
        self.assertEqual(decision["position_now"], 0)
        self.assertIn("100股整数倍", "；".join(decision["reasons"]))


if __name__ == "__main__":
    unittest.main()
