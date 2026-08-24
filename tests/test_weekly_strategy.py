import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from weekly_strategy import (
    analyze_weekly_trend,
    build_holding_action,
    build_weekly_plan,
    load_account_state,
    position_plan,
    save_weekly_plan,
)


def rising_frame(start=10.0, days=120):
    dates = pd.date_range("2026-03-02", periods=days, freq="B")
    closes = [start + index * 0.03 for index in range(days)]
    return pd.DataFrame({
        "date": dates,
        "open": [value * 0.995 for value in closes],
        "high": [value * 1.01 for value in closes],
        "low": [value * 0.97 for value in closes],
        "close": closes,
        "volume": [1_000_000] * days,
    })


class WeeklyStrategyTest(unittest.TestCase):
    def test_previous_seven_percent_loss_enters_recovery_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "account.json"
            path.write_text(
                '{"equity":50000,"last_week_pnl":-3500,"last_week_end":"2026-08-21",'
                '"current_week_pnl":0,"holdings_status":"已确认","holdings":[]}',
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"ACCOUNT_STATE_JSON": ""}):
                account = load_account_state(path, datetime(2026, 8, 24, 8, 0))
        self.assertEqual(account["risk_profile"]["name"], "恢复期")
        self.assertEqual(account["risk_profile"]["max_total_pct"], 0.30)
        self.assertEqual(account["risk_profile"]["risk_per_trade"], 200)
        self.assertTrue(account["can_open_new"])

    def test_missing_holdings_does_not_block_account_level_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "account.json"
            path.write_text('{"equity":50000,"holdings_status":"待录入","holdings":[]}', encoding="utf-8")
            with patch.dict("os.environ", {"ACCOUNT_STATE_JSON": ""}):
                account = load_account_state(path, datetime(2026, 8, 24, 8, 0))
        self.assertTrue(account["can_open_new"])
        self.assertEqual(account["block_reasons"], [])
        self.assertFalse(account["holdings_tracking_enabled"])

    def test_confirmed_holding_keeps_private_position_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "account.json"
            path.write_text(
                '{"equity":45682.77,"available_cash":35250.77,"holdings_status":"已确认",'
                '"holdings":[{"code":"000933","name":"样本","quantity":400,'
                '"available_quantity":300,"cost_price":25.9803,"current_price":26.08}]}',
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"ACCOUNT_STATE_JSON": ""}):
                account = load_account_state(path, datetime(2026, 8, 24, 8, 0))
        self.assertTrue(account["holdings_tracking_enabled"])
        self.assertEqual(account["holdings"][0]["available_quantity"], 300)
        self.assertEqual(account["holdings"][0]["cost_price"], 25.9803)
        self.assertEqual(account["holdings_pct"], 22.84)

    def test_current_week_two_percent_loss_freezes_opening(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "account.json"
            path.write_text(
                '{"equity":50000,"current_week_pnl":-1000,"holdings_status":"已确认","holdings":[]}',
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"ACCOUNT_STATE_JSON": ""}):
                account = load_account_state(path, datetime(2026, 8, 24, 8, 0))
        self.assertTrue(account["current_week_frozen"])
        self.assertFalse(account["can_open_new"])

    def test_position_is_rounded_down_to_board_lot_and_risk_budget(self):
        account = {
            "equity": 50000, "available_cash": 50000, "holdings_value": 0,
            "can_open_new": True, "block_reasons": [],
            "risk_profile": {"max_total_pct": 0.6, "max_stock_pct": 0.2, "risk_per_trade": 300},
        }
        trend = {"qualified": True, "entry_zone": {"low": 19.9, "high": 20.1}, "stop_price": 19.0}
        plan = position_plan(trend, account, "主选")
        self.assertEqual(plan["quantity"], 300)
        self.assertEqual(plan["planned_loss"], 300)
        self.assertTrue(plan["executable"])

    def test_trend_analysis_rejects_overextended_price(self):
        frame = rising_frame()
        frame.loc[frame.index[-1], ["open", "high", "low", "close"]] = [20, 22, 19.8, 21.5]
        result = analyze_weekly_trend(frame, rising_frame(start=10.0))
        self.assertTrue(result["available"])
        self.assertTrue(result["overextended"])
        self.assertFalse(result["qualified"])

    def test_same_week_plan_keeps_frozen_codes(self):
        account = {
            "equity": 50000, "available_cash": 50000, "holdings_value": 0,
            "holdings_complete": True, "can_open_new": True, "block_reasons": [],
            "risk_profile": {"max_total_pct": 0.6, "max_stock_pct": 0.2, "risk_per_trade": 300},
        }
        trend = {"qualified": True, "entry_zone": {"low": 10, "high": 10}, "stop_price": 9.5}
        old = {"plan_id": "2026-W35", "selections": [{"code": "000001", "role": "主选"}]}
        candidates = [
            {"code": "000001", "name": "旧名单", "industry": "银行", "weekly_trend": trend, "weekly_evaluation": {"eligible": True, "score": 60, "components": {}}},
            {"code": "000002", "name": "新高分", "industry": "科技", "weekly_trend": trend, "weekly_evaluation": {"eligible": True, "score": 99, "components": {}}},
        ]
        plan = build_weekly_plan(candidates, account, {}, datetime(2026, 8, 24, 8), existing=old)
        self.assertTrue(plan["frozen"])
        self.assertEqual([row["code"] for row in plan["selections"]], ["000001"])

    def test_broken_trend_near_cost_reduces_position(self):
        account = {"current_week_frozen": False, "broker_conditional_orders": "已确认"}
        holding = {"code": "600584", "name": "样本", "quantity": 500, "cost_price": 30, "stop_price": 27}
        action = build_holding_action(holding, {"qualified": False, "close": 29.8}, account)
        self.assertEqual(action["action"], "至少减仓50%")
        self.assertEqual(action["sell_quantity"], 300)
        self.assertFalse(action["t_eligible"])

    def test_missing_market_data_never_creates_sell_instruction(self):
        account = {"current_week_frozen": False, "broker_conditional_orders": "待确认"}
        holding = {"code": "000933", "name": "样本", "quantity": 400, "available_quantity": 400, "cost_price": 25}
        action = build_holding_action(holding, {"available": False, "qualified": False}, account)
        self.assertEqual(action["action"], "等待行情复核")
        self.assertEqual(action["sell_quantity"], 0)

    def test_saved_weekly_plan_does_not_publish_private_holdings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weekly.json"
            saved = save_weekly_plan({
                "plan_id": "2026-W35",
                "selections": [],
                "holding_actions": [{"code": "600584", "cost_price": 30}],
                "account": {
                    "equity": 50000, "holdings": [{"code": "600584", "cost_price": 30}],
                    "holdings_status": "已确认", "holdings_complete": True,
                    "risk_profile": {"name": "恢复期"}, "can_open_new": True, "block_reasons": [],
                },
            }, path)
        self.assertEqual(saved["holding_actions"], [])
        self.assertNotIn("holdings", saved["account"])


if __name__ == "__main__":
    unittest.main()
