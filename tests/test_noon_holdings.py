import unittest
from datetime import datetime
from unittest.mock import Mock, patch
import pandas as pd

from data_feed import DataFeed
from noon_holdings import build_noon_action


def quote(price=11, date="20260922", time="1130"):
    return {"available": True, "trade_date": date, "morning_session": {
        "completed": True, "last_time": time, "open": 10, "close": price,
        "vwap": 10.5, "volume": 100, "change_pct": 10}}


class NoonHoldingsTest(unittest.TestCase):
    @patch("research_core.analyze_weekly_trend")
    @patch("research_core.datetime")
    @patch("research_core.DataFeed")
    def test_pipeline_fetches_each_holding_and_its_industry(self, factory, clock, trend):
        from research_core import build_account_holding_actions
        from email_digest import build_email
        feed = factory.return_value
        clock.now.return_value = datetime(2026, 9, 22, 12)
        feed.get_kline.return_value = pd.DataFrame()
        feed.get_stock_industries.return_value = {"600183": "电子"}
        feed.get_sector_fund_flow.return_value = pd.DataFrame([{"name": "电子", "code": "BK001"}])
        feed.get_index_morning.return_value = quote()
        feed.get_intraday_minute.return_value = quote(8.9)
        trend.return_value = {"available": True, "qualified": True, "close": 10, "stop_price": 9}
        actions = build_account_holding_actions({"holdings_tracking_enabled": True, "holdings": [
            {"code": "600183", "name": "样本", "quantity": 100, "available_quantity": 0, "cost_price": 10}]})
        feed.get_intraday_minute.assert_called_once_with("600183")
        self.assertEqual([c.args[0] for c in feed.get_index_morning.call_args_list], ["000300", "BK001"])
        self.assertEqual(actions[0]["sell_quantity"], 0)
        self.assertEqual(actions[0]["sector_name"], "电子")
        message = build_email({"weekly_plan": {"holding_actions": actions}})
        self.assertIn("2026-09-22 11:30", message.get_body(preferencelist=("plain",)).get_content())
        self.assertIn("2026-09-22 11:30", message.get_body(preferencelist=("html",)).get_content())

    def action(self, stock=None, market=None, sector=None, now=None):
        return build_noon_action(
            {"code": "600183", "quantity": 100, "available_quantity": 100, "cost_price": 10},
            {"available": True, "qualified": True, "close": 10, "stop_price": 9}, {},
            stock if stock is not None else quote(), market if market is not None else quote(),
            sector if sector is not None else quote(), now or datetime(2026, 9, 22, 12), "电子")

    def test_morning_price_triggers_stop_not_yesterday_close(self):
        action = self.action(stock=quote(8.9))
        self.assertEqual(action["action"], "清仓")
        self.assertEqual(action["sell_quantity"], 100)
        self.assertEqual(action["reference_price"], 8.9)
        self.assertEqual(action["quote_as_of"], "2026-09-22 11:30")

    def test_stale_missing_and_incomplete_sources_block_quantity(self):
        for kwargs in ({"stock": quote(date="20260921")}, {"market": {}},
                       {"sector": quote(time="1129")}, {"now": datetime(2026, 9, 22, 10)},
                       {"stock": quote(float("nan"))}):
            with self.subTest(kwargs=kwargs):
                action = self.action(**kwargs)
                self.assertFalse(action["morning_ready"])
                self.assertEqual(action["sell_quantity"], 0)
                self.assertIsNone(action["reference_price"])

    def test_sector_weakness_changes_holding_advice(self):
        action = self.action(sector=quote(10.1))
        self.assertEqual(action["action"], "持有观察，暂缓加仓")
        self.assertIn("行业板块", action["reason"])
        self.assertEqual(action["sell_quantity"], 0)

    @patch.object(DataFeed, "_request")
    def test_index_parser_excludes_afternoon_and_preserves_date(self, request):
        entries = [f"2026-09-22 10:{i:02d},10,10,10,10,100,100000,10" for i in range(30)]
        entries += ["2026-09-22 11:30,10,11,11,10,100,100000,10",
                    "2026-09-22 13:00,11,99,99,11,100,100000,11"]
        request.return_value = Mock()
        request.return_value.json.return_value = {"data": {"trends": entries}}
        result = DataFeed().get_index_morning("000300")
        self.assertEqual(result["trade_date"], "20260922")
        self.assertEqual(result["morning_session"]["close"], 11)
        self.assertEqual(result["morning_session"]["last_time"], "1130")


if __name__ == "__main__":
    unittest.main()
