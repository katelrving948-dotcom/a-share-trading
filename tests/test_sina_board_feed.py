import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo
import pandas as pd

from data_feed import DataFeed
from sina_board_feed import SinaIndustryFeed


TODAY = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
YESTERDAY = (datetime.fromisoformat(TODAY) - timedelta(days=1)).strftime("%Y-%m-%d")


class SinaBoardFeedTest(unittest.TestCase):
    def setUp(self):
        clock_patch = patch("sina_board_feed.datetime", wraps=datetime)
        self.clock = clock_patch.start()
        self.clock.now.return_value = datetime.fromisoformat(TODAY).replace(hour=12)
        self.addCleanup(clock_patch.stop)

    def source(self, date=YESTERDAY, minute_date=TODAY, minute_time="11:30:00"):
        source = SinaIndustryFeed(Mock())
        source._read = Mock(side_effect=[
            [{"category": "hangye_ZC39", "name": "电子制造"}],
            [{"opendate": date, "r0_net": "200000000", "netamount": "900000000",
              "r0_ratio": "0.05", "avg_changeratio": "0.012", "avg_price": "10"}],
            ["121", [{"opendate": minute_date, "ticktime": minute_time,
                      "netamount": "900000000", "ratioamount": "0.225", "r0_ratio": "0.05",
                      "avg_changeratio": "0.012", "avg_price": "10"},
                     {"opendate": minute_date, "ticktime": "13:01:00", "avg_price": "999"}]],
        ])
        return source

    def test_main_flow_units_and_current_csrc_classification(self):
        source = self.source()
        rows = source.load()
        self.assertEqual(len(rows), 1)
        row = rows.iloc[0]
        self.assertEqual(row["main_net_inflow"], 2)
        self.assertEqual(row["main_net_pct"], 5)
        self.assertEqual(row["change_pct"], 1.2)
        self.assertEqual(row["code"], "sina:hangye_ZC39")
        self.assertEqual(row["trade_date"], TODAY)
        self.assertEqual(source._read.call_args_list[0].args[1]["fenlei"], 2)
        self.assertEqual(source.flow_history(row["code"], 5)["days"], 1)
        source.load()
        self.assertEqual(source._read.call_count, 3)
        self.assertEqual(row["as_of"], TODAY + " 11:30")
        self.assertTrue(row["main_net_estimated"])

    def test_midday_accepts_yesterday_history_only_with_today_1130(self):
        self.assertEqual(len(self.source(date=YESTERDAY).load()), 1)
        self.assertTrue(self.source(minute_date=YESTERDAY).load().empty)
        self.assertTrue(self.source(minute_time="11:29:00").load().empty)

    def test_early_run_and_same_day_close_do_not_leak_into_noon_report(self):
        self.clock.now.return_value = datetime.fromisoformat(TODAY).replace(hour=10)
        self.assertTrue(self.source().load().empty)
        source = self.source()
        source.date = TODAY
        source.history["test"] = [{"opendate": TODAY, "r0_net": "9900000000"},
                                  {"opendate": YESTERDAY, "r0_net": "100000000"}]
        self.assertEqual(source.flow_history("test", 5)["recent_main_net_inflow"], 1)

    def test_stale_history_is_not_current_data(self):
        source = self.source("2020-06-30")
        self.assertTrue(source.load().empty)

    def test_live_members_produce_named_leaders_for_email(self):
        feed = DataFeed()
        feed._stock_list_cache = pd.DataFrame([
            {"code": "600183", "name": "样本", "is_st": False, "board": "主板",
             "price": 10, "market_cap": 100, "amount": 100000, "change_pct": 1,
             "main_net_pct": 2, "main_net": 1000}])
        leaders = feed._rank_board_leaders({"600183"})
        self.assertEqual(leaders[0]["leadership_role"], "龙头")

    @patch.object(DataFeed, "get_concept_fund_flow")
    @patch.object(DataFeed, "_request_eastmoney")
    def test_noon_source_does_not_mix_partial_eastmoney_chain(self, eastmoney, concepts):
        feed = DataFeed(morning_sectors=True)
        feed._sina_industries = self.source()
        feed._sina_industries.members["sina:hangye_ZC39"] = {"600183"}
        feed._sina_industries.constituents = Mock(return_value={"600183"})
        feed._rank_board_leaders = Mock(return_value=[{"code": "600183"}])
        result = feed.get_rotation_matches(["600183"])
        self.assertEqual(result["boards"][0]["member_count"], 1)
        self.assertEqual(result["boards"][0]["as_of"], TODAY + " 11:30")
        eastmoney.assert_not_called()
        concepts.assert_not_called()

    def test_nonfinite_values_are_rejected(self):
        source = self.source()
        source._read.side_effect = [[{"category": "hangye_ZC39", "name": "电子"}],
                                    [{"opendate": YESTERDAY}],
                                    ["121", [{"opendate": TODAY, "ticktime": "11:30:00",
                                              "netamount": "NaN", "ratioamount": "0.1", "r0_ratio": "0.1",
                                              "avg_changeratio": "0.1", "avg_price": "10"}]]]
        self.assertTrue(source.load().empty)

    def test_constituents_pagination_and_partial_failure(self):
        source = SinaIndustryFeed(Mock())
        page = [{"symbol": f"sz{i:06d}"} for i in range(100)]
        source._read = Mock(side_effect=["101", page, [{"symbol": "sh600183"}]])
        self.assertEqual(len(source.constituents("sina:hangye_ZC39")), 101)
        self.assertEqual(source._read.call_args.args[1]["page"], 2)
        self.assertEqual(source._read.call_args.args[1]["bankuai"], "hangye_ZC39")
        source.members = {}
        source._read.side_effect = ["101", page, None]
        self.assertEqual(source.constituents("sina:hangye_ZC39"), set())

    @patch.object(DataFeed, "_request_eastmoney", return_value=(None, ""))
    def test_industry_fallback_drives_members_history_and_mapping(self, _):
        feed = DataFeed()
        feed._sina_industries = self.source()
        rows = feed.get_sector_fund_flow()
        self.assertEqual(len(rows), 1)
        feed._sina_industries.members["sina:hangye_ZC39"] = {"600183"}
        self.assertEqual(feed.get_board_constituents("sina:hangye_ZC39"), {"600183"})
        self.assertEqual(feed.get_stock_industries(["600183"]), {"600183": "电子制造"})
        self.assertEqual(feed.get_board_flow_history("sina:hangye_ZC39")["recent_main_net_inflow"], 2)
        self.assertFalse(feed.get_index_morning("sina:hangye_ZC39")["available"])


if __name__ == "__main__":
    unittest.main()
