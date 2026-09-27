import copy
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from paper_trading import SHANGHAI, execute_session, fee, load_state, new_state
from weekly_strategy import week_identity


class PaperTradingTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 22, 13, 5, tzinfo=SHANGHAI)
        self.trend = {"available": True, "qualified": True, "trend_qualified": True,
                      "as_of": "2026-09-21", "close": 10, "stop_price": 9.5,
                      "pullback_plan": {"state": "confirmed", "confirmed_on": "2026-09-18",
                                        "support_low": 9.8, "max_entry_price": 10.2,
                                        "stop_price": 9.5, "anchor_high": 11.5}}
        self.plan = {"plan_id": week_identity(self.now)["plan_id"], "frozen": True,
                     "selections": [{"code": "000001", "name": "样例", "role": "主选",
                                     "status": "可执行", "weekly_score": 90, "weekly_trend": self.trend}]}
        self.quotes = {"000001": {"available": True, "trade_date": "20260922", "last_time": "13:05", "close_price": 10, "low": 9.9}}

    def buy(self):
        return execute_session(new_state(), self.plan, self.quotes, {}, self.now)

    def test_modes_cash_conservation_and_risk_budget(self):
        result = self.buy()
        small, large = result["accounts"].values()
        self.assertGreater(small["holdings"][0]["quantity"] * 10.01, 49000)
        self.assertLessEqual(large["holdings"][0]["quantity"] * .51, 3000)
        for book in result["accounts"].values():
            self.assertGreaterEqual(book["cash"], 0)
            trade = book["trades"][0]
            self.assertAlmostEqual(book["initial"], book["cash"] + trade["price"] * trade["quantity"] + trade["fee"])
            self.assertLess(book["equity"], book["initial"])

    def test_repeat_does_not_duplicate_any_trade_or_curve(self):
        first = self.buy()
        self.assertEqual(first, execute_session(first, self.plan, self.quotes, {}, self.now))

    def test_bad_quotes_and_unfrozen_plan_do_not_buy(self):
        for key, value in [("trade_date", "20260921"), ("last_time", "11:30"), ("close_price", float("nan")), ("last_time", "13:06")]:
            quotes = copy.deepcopy(self.quotes)
            quotes["000001"][key] = value
            result = execute_session(new_state(), self.plan, quotes, {}, self.now)
            self.assertFalse(result["accounts"]["aggressive"]["trades"])
        self.plan["frozen"] = False
        self.assertFalse(self.buy()["accounts"]["aggressive"]["trades"])

    def test_holiday_after_hours_and_withdrawn_are_blocked(self):
        for now in [self.now.replace(day=25), self.now.replace(hour=15)]:
            result = execute_session(new_state(), self.plan, self.quotes, {}, now)
            self.assertFalse(result["accounts"]["aggressive"]["trades"])
        self.plan["selections"][0]["status"] = "撤销"
        self.assertFalse(self.buy()["accounts"]["aggressive"]["trades"])

    def sell_session(self, state, day, price, ready=True):
        now = self.now.replace(day=day)
        trend = {**self.trend, "as_of": (now.date()-timedelta(days=1)).isoformat(), "close": price}
        quote = {**self.quotes["000001"], "trade_date": now.strftime("%Y%m%d"), "close_price": price}
        return execute_session(state, {}, {"000001": quote}, {"000001": {"morning_ready": ready, "weekly_trend": trend}}, now)

    def test_staged_profit_once_and_skip_to_two_is_cumulative(self):
        bought = self.buy()
        result = self.sell_session(bought, 23, 11.4)
        for book in result["accounts"].values():
            holding = book["holdings"][0]
            self.assertEqual(holding["profit_stage"], 2)
            self.assertEqual(holding["profit_sold"], int(holding["initial_quantity"] * 2 / 3 / 100) * 100)
        again = self.sell_session(result, 24, 11.4)
        self.assertEqual(len(again["accounts"]["aggressive"]["trades"]), 2)

    def test_stop_t_plus_one_and_missing_context(self):
        bought = self.buy()
        result = self.sell_session(bought, 23, 9.4)
        self.assertFalse(result["accounts"]["aggressive"]["holdings"])
        self.assertEqual(result["accounts"]["aggressive"]["trades"][-1]["side"], "sell")
        missing = self.sell_session(bought, 23, 9.4, False)
        self.assertEqual(len(missing["accounts"]["aggressive"]["trades"]), 1)
        bought.pop("last_session")
        review = {"000001": {"morning_ready": True, "weekly_trend": self.trend}}
        quote = {"000001": {**self.quotes["000001"], "close_price": 9.4}}
        same_day = execute_session(bought, {}, quote, review, self.now)
        self.assertEqual(len(same_day["accounts"]["aggressive"]["trades"]), 1)

    def test_no_second_stock_in_full_position_mode(self):
        second = copy.deepcopy(self.plan["selections"][0]); second["code"] = "000002"
        self.plan["selections"].append(second)
        self.quotes["000002"] = dict(self.quotes["000001"])
        result = self.buy()
        self.assertEqual(len(result["accounts"]["aggressive"]["holdings"]), 1)
        self.assertEqual(len(result["accounts"]["balanced"]["holdings"]), 2)

    def test_corrupt_ledger_must_not_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.json"
            path.write_text("broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_state(path)

    def test_weekly_loss_freezes_new_risk_and_fee(self):
        state = new_state()
        for book in state["accounts"].values():
            book.update(week_id=self.plan["plan_id"], cash=book["initial"] * .97)
        result = execute_session(state, self.plan, self.quotes, {}, self.now)
        self.assertFalse(result["accounts"]["aggressive"]["trades"])
        self.assertEqual(fee(1000, "buy"), 5)
        self.assertEqual(fee(1000, "sell"), 5.5)

    def test_price_limit_and_slippage_cannot_bypass_entry(self):
        self.trend["close"] = 9
        self.assertFalse(self.buy()["accounts"]["aggressive"]["trades"])
        self.trend["close"] = 10
        self.trend["pullback_plan"]["max_entry_price"] = 10
        self.assertFalse(self.buy()["accounts"]["aggressive"]["trades"])

    def test_sell_cash_reconciles_and_missing_marks_block_new_risk(self):
        bought = self.buy()
        sold = self.sell_session(bought, 23, 9.4)
        for book in sold["accounts"].values():
            cash = book["initial"]
            for trade in book["trades"]:
                cash += (1 if trade["side"] == "sell" else -1) * trade["quantity"] * trade["price"] - trade["fee"]
            self.assertAlmostEqual(cash, book["cash"])
        missing = execute_session(bought, self.plan, {}, {}, self.now.replace(day=23))
        self.assertFalse(missing["accounts"]["balanced"]["valuation_complete"])
        self.assertEqual(len(missing["accounts"]["balanced"]["trades"]), 1)


if __name__ == "__main__":
    unittest.main()
