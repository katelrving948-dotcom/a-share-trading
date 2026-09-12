import copy
import unittest
from unittest.mock import MagicMock
from datetime import datetime

import pandas as pd

from config import LONG_TERM
from fundamental import FundamentalScorer
from pullback_strategy import build_pullback_plan, entry_gate, holding_horizon
from research_core import _cap_scan, build_market_research, build_trade_decision
from weekly_strategy import analyze_weekly_trend, build_weekly_plan, position_plan, score_weekly_candidate


def anchor():
    return {"version": 1, "formed_on": "2026-09-07", "support_low": 10.8,
            "support_high": 11.2, "anchor_high": 13.0, "anchor_atr": 0.4,
            "stop_price": 10.6, "max_entry_price": 11.4, "valid_sessions": 5,
            "state": "waiting_pullback"}


def bars(extra=()):
    dates = pd.bdate_range(end="2026-09-07", periods=70)
    prices = [9 + i * .03 for i in range(70)]
    rows = [{"date": day, "open": p, "high": p + .2, "low": p - .2, "close": p} for day, p in zip(dates, prices)]
    for day, low, close in extra:
        rows.append({"date": pd.Timestamp(day), "open": close, "high": close + .2, "low": low, "close": close})
    return pd.DataFrame(rows)


def confirmed_trend():
    plan = anchor()
    plan.update(state="confirmed", touched_on="2026-09-08", confirmed_on="2026-09-09")
    return {"available": True, "qualified": True, "trend_qualified": True,
            "as_of": "2026-09-09", "pullback_plan": plan,
            "entry_zone": {"low": 10.8, "high": 11.4}, "stop_price": 10.6}


def account():
    return {"equity": 10000, "available_cash": 10000, "holdings_value": 0,
            "can_open_new": True, "block_reasons": [],
            "risk_profile": {"max_total_pct": .6, "max_stock_pct": .2, "risk_per_trade": 300}}


def quote():
    return {"available": True, "trade_date": "20260910", "last_time": "1300", "close_price": 11.1, "low": 11.0}


class PullbackTest(unittest.TestCase):
    def test_touch_is_not_confirmation_and_later_bar_confirms(self):
        touch = bars([("2026-09-08", 11.0, 11.1)])
        first = build_pullback_plan(touch, 12, 1, anchor())
        self.assertEqual(first["state"], "waiting_confirmation")
        full = bars([("2026-09-08", 11.0, 11.1), ("2026-09-09", 11.02, 11.24)])
        result = build_pullback_plan(full, 15, 2, first)
        self.assertEqual(result["state"], "confirmed")
        self.assertEqual(result["support_high"], 11.2)
        self.assertEqual(result["confirmed_on"], "2026-09-09")
        self.assertEqual(build_pullback_plan(full, 16, 3, result), result)

    def test_initial_plan_does_not_retroactively_buy(self):
        result = build_pullback_plan(bars([("2026-09-08", 11, 11.1)]), 11, .4)
        self.assertEqual(result["state"], "waiting_pullback")
        self.assertIsNone(result["touched_on"])

    def test_break_support_and_expiry_are_terminal(self):
        broken = build_pullback_plan(bars([("2026-09-08", 10.5, 11.3)]), 11, .4, anchor())
        self.assertEqual(broken["state"], "invalidated")
        recovered = bars([("2026-09-08", 10.5, 11.3), ("2026-09-09", 11.1, 11.4)])
        self.assertEqual(build_pullback_plan(recovered, 11, .4, broken)["state"], "invalidated")
        extra = [(str(x.date()), 11.7, 12) for x in pd.bdate_range("2026-09-08", periods=5)]
        self.assertEqual(build_pullback_plan(bars(extra), 11, .4, anchor())["state"], "expired")

    def test_live_entry_requires_all_price_and_time_conditions(self):
        now = datetime(2026, 9, 10, 13, 1)
        self.assertTrue(entry_gate(confirmed_trend(), quote(), now)["passed"])
        for change in ({"close_price": 11.6}, {"low": 10.5}, {"trade_date": "20260909"},
                       {"last_time": "11:30"}, {"last_time": None}, {"close_price": float("nan")},
                       {"close_price": 11.39}):  # reward to prior high fails 1.5R only with nearer target below
            q = {**quote(), **change}
            if change == {"close_price": 11.39}:
                t = confirmed_trend(); t["pullback_plan"]["anchor_high"] = 12
            else:
                t = confirmed_trend()
            self.assertFalse(entry_gate(t, q, now)["passed"], change)
        self.assertFalse(entry_gate(confirmed_trend(), quote(), datetime(2026, 9, 10, 12))["passed"])
        t = confirmed_trend(); t["pullback_plan"]["confirmed_on"] = "2026-09-10"
        self.assertFalse(entry_gate(t, quote(), now)["passed"])
        t = confirmed_trend(); t["as_of"] = "2026-09-07"
        self.assertFalse(entry_gate(t, quote(), now)["passed"])

    def test_no_pullback_or_broken_trend_never_gets_entry(self):
        for change in ("waiting_pullback", "waiting_confirmation", "expired", "invalidated"):
            t = confirmed_trend(); t["pullback_plan"]["state"] = change
            self.assertFalse(entry_gate(t, quote(), datetime(2026, 9, 10, 13, 1))["passed"])
        t = confirmed_trend(); t["trend_qualified"] = False
        self.assertFalse(entry_gate(t, quote(), datetime(2026, 9, 10, 13, 1))["passed"])

    def test_incomplete_today_cannot_confirm(self):
        frame = bars([("2026-09-08", 11, 11.1), ("2026-09-09", 11.02, 11.24)])
        result = analyze_weekly_trend(frame, bars(), anchor(), datetime(2026, 9, 9, 12))
        self.assertEqual(result["as_of"], "2026-09-08")
        self.assertEqual(result["pullback_plan"]["state"], "waiting_confirmation")

    def test_small_cap_pool_expands_but_liquidity_and_st_still_apply(self):
        rows = [{"code": str(i), "market_cap": cap, "amount": amount, "is_st": st,
                 "price": 10, "turnover_rate": 5, "board": "主板"}
                for i, (cap, amount, st) in enumerate([(25, 6e7, False), (19, 6e7, False), (25, 1e7, False), (25, 6e7, True), (600, 3e7, False)])]
        pool = FundamentalScorer._candidate_pool(pd.DataFrame(rows))
        self.assertEqual(set(pool["code"]), {"0", "4"})
        self.assertEqual(LONG_TERM["market_cap_min"], 20)
        self.assertEqual(holding_horizon(25)["label"], "3-10个交易日")
        self.assertEqual(holding_horizon(600)["label"], "5-20个交易日")

    def test_scan_preserves_small_and_frozen_names(self):
        rows = [{"code": str(i), "market_cap": 600} for i in range(10)] + [{"code": "small", "market_cap": 25, "fundamental_score": 70}]
        result = _cap_scan(rows, 3, {"9"})
        self.assertEqual(len(result), 3)
        self.assertTrue({"9", "small"}.issubset({r["code"] for r in result}))

    def test_fundamentals_cannot_be_replaced_with_price_strength(self):
        item = {"weekly_trend": confirmed_trend(), "fundamental_score": 59,
                "sector_adjusted_fundamental_score": 90, "board_strength_score": 90}
        self.assertFalse(score_weekly_candidate(item)["eligible"])
        item["fundamental_score"] = 70
        self.assertTrue(score_weekly_candidate(item)["eligible"])
        item["financial_risk"] = {"hard_block": True}
        self.assertFalse(score_weekly_candidate(item)["eligible"])

    def test_weekly_entry_cannot_bypass_quality_account_or_withdrawal(self):
        item = {"code": "000001", "name": "样本", "market_cap": 25,
                "weekly_trend": confirmed_trend(), "entry_quote": quote(),
                "weekly_evaluation": {"eligible": True, "score": 90}}
        now = datetime(2026, 9, 10, 13, 1)
        plan = build_weekly_plan([item], account(), {}, now)
        self.assertTrue(plan["selections"][0]["position_plan"]["executable"])
        frozen = copy.deepcopy(plan); frozen["selections"][0]["status"] = "撤销"
        again = build_weekly_plan([item], account(), {}, now, frozen)
        self.assertFalse(again["selections"][0]["position_plan"]["executable"])
        removed = build_weekly_plan([], account(), {}, now, plan)
        self.assertFalse(removed["selections"][0]["position_plan"]["executable"])
        item["weekly_evaluation"] = {"eligible": False, "reasons": ["基本面不合格"]}
        blocked = build_weekly_plan([item], account(), {}, now, plan)
        self.assertFalse(blocked["selections"][0]["position_plan"]["executable"])
        a = account(); a["can_open_new"] = False
        self.assertFalse(position_plan(confirmed_trend(), a, "主选", gate={"passed": True, "reference_price": 11.1})["executable"])

    def test_small_account_risk_and_star_board_minimum(self):
        t = confirmed_trend()
        p = position_plan(t, account(), "主选", gate={"passed": True, "reference_price": 11.1})
        self.assertEqual(p["risk_budget"], 60)
        self.assertLessEqual(p["planned_loss"], 60)
        star = position_plan(t, account(), "主选", gate={"passed": True, "reference_price": 11.1}, code="688001")
        self.assertEqual(star["quantity"], 0)
        self.assertFalse(star["executable"])
        self.assertFalse(position_plan(t, account(), "主选")["executable"])

    def test_legacy_breakout_is_not_entry(self):
        decision = build_trade_decision({"fundamental_score": 90, "board_strength_score": 90,
                                       "morning_plan": {"actionable": True, "execution_state": "已触发突破确认"}})
        self.assertFalse(decision["entry_gate"]["passed"])
        vwap_only = build_trade_decision({"fundamental_score": 90, "board_strength_score": 90,
                                         "morning_plan": {"actionable": True, "execution_state": "当前价进入回踩进场区"}})
        self.assertFalse(vwap_only["entry_gate"]["passed"])

    def test_two_primary_names_cannot_reserve_the_same_cash(self):
        a = account(); a["available_cash"] = 1500
        first = position_plan(confirmed_trend(), a, "主选", gate={"passed": True, "reference_price": 11.1})
        second = position_plan(confirmed_trend(), a, "主选", first["estimated_value"],
                               gate={"passed": True, "reference_price": 11.1})
        self.assertEqual(first["quantity"], 100)
        self.assertEqual(second["quantity"], 0)

    def test_research_pipeline_carries_frozen_support_and_current_quote(self):
        feed = MagicMock()
        feed._stock_list_cache = None
        feed.get_financial_news.return_value = []
        feed.get_external_market_context.return_value = {}
        feed.get_market_context.return_value = {}
        feed.get_stock_industries.return_value = {"000001": "科技"}
        board = {"name": "科技", "type": "行业", "flow_score": 80, "rotation_score": 80}
        feed.get_rotation_matches.return_value = {"boards": [board], "matches": {"000001": [board]}}
        stock = bars([("2026-09-08", 11, 11.1), ("2026-09-09", 11.02, 11.24)])
        benchmark = stock.copy()
        benchmark[["open", "high", "low", "close"]] = 10.0
        feed.get_kline.side_effect = lambda code, count: benchmark if code == "000300" else stock
        feed.get_balance_sheet_data.return_value = {"available": False}
        feed.get_intraday_minute.return_value = quote()
        feed.get_intraday_stock_fund_flow.return_value = {"available": False}
        rows = [{"code": "000001", "name": "样本", "industry": "科技", "market_cap": 25,
                 "fundamental_score": 80, "technical_score": None}]
        old = {"plan_id": "2026-W37", "selections": [{"code": "000001", "role": "主选",
                "weekly_trend": {"pullback_plan": anchor()}}]}
        now = datetime(2026, 9, 10, 13, 1)
        build_market_research(rows, {"rows": rows}, {}, feed=feed, existing_plan=old, now=now)
        self.assertEqual(rows[0]["weekly_trend"]["pullback_plan"]["formed_on"], "2026-09-07")
        self.assertEqual(rows[0]["weekly_trend"]["pullback_plan"]["state"], "confirmed")
        self.assertEqual(rows[0]["entry_quote"]["last_time"], "1300")
        self.assertEqual(rows[0]["holding_plan"]["label"], "3-10个交易日")
        plan = build_weekly_plan(rows, account(), {}, now, old)
        self.assertEqual(plan["selections"][0]["status"], "可执行")


if __name__ == "__main__":
    unittest.main()
