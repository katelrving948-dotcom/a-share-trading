import unittest

from data_feed import DataFeed
from fundamental import build_opening_entry_plan


class OpeningEntryPlanTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
