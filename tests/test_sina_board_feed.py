import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from data_feed import DataFeed
from sina_board_feed import SinaIndustryFeed


TODAY = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


class SinaBoardFeedTest(unittest.TestCase):
    def source(self, date=TODAY):
        source = SinaIndustryFeed(Mock())
        source._read = Mock(side_effect=[
            [{"category": "hangye_ZC39", "name": "电子制造"}],
            [{"opendate": date, "r0_net": "200000000", "netamount": "900000000",
              "r0_ratio": "0.05", "avg_changeratio": "0.012", "avg_price": "10"}],
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
        self.assertEqual(source._read.call_count, 2)

    def test_stale_history_is_not_current_data(self):
        source = self.source("2020-06-30")
        self.assertTrue(source.load().empty)

    def test_nonfinite_values_are_rejected(self):
        source = self.source()
        source._read.side_effect = [[{"category": "hangye_ZC39", "name": "电子"}],
                                    [{"opendate": TODAY, "r0_net": "NaN", "r0_ratio": "0.1",
                                      "avg_changeratio": "0.1", "avg_price": "10"}]]
        self.assertTrue(source.load().empty)

    def test_constituents_pagination_and_partial_failure(self):
        source = SinaIndustryFeed(Mock())
        page = [{"code": f"{i:06d}"} for i in range(100)]
        source._read = Mock(side_effect=["101", page, [{"code": "600183"}]])
        self.assertEqual(len(source.constituents("sina:hangye_ZC39")), 101)
        self.assertEqual(source._read.call_args.args[1]["page"], 2)
        self.assertEqual(source._read.call_args.args[1]["node"], "hangye_ZC39")
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
