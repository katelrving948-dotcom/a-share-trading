import unittest

import pandas as pd

from data_feed import DataFeed
from fundamental import LongTermFundamentalScreener


class _HotCoreFeed:
    @staticmethod
    def get_financials_batch(codes):
        return {
            code: {
                "code": code, "name": "中际旭创" if code == "300308" else "候选二",
                "available": True, "report_date": "2026-03-31",
                "annualized_roe": 20, "roe": 5, "revenue_growth": 30,
                "profit_growth": 40, "gross_margin": 40, "eps": 2,
                "operating_cf_per_share": 2, "pe": 45, "pb": 6,
                "price": 864 if code == "300308" else 50,
                "market_cap": 1000, "main_net": 2, "main_net_pct": 3,
            }
            for code in codes
        }


class HotCoreTest(unittest.TestCase):
    def test_board_leaders_include_high_price_stock(self):
        feed = DataFeed.__new__(DataFeed)
        feed._stock_list_cache = pd.DataFrame([
            {"code": "300308", "name": "中际旭创", "price": 864,
             "market_cap": 1000, "amount": 100, "change_pct": 5,
             "main_net_pct": 4, "main_net": 20e8, "is_st": False, "board": "创业板"},
            {"code": "000002", "name": "候选二", "price": 50,
             "market_cap": 800, "amount": 80, "change_pct": 3,
             "main_net_pct": 2, "main_net": 10e8, "is_st": False, "board": "深圳主板"},
            {"code": "000003", "name": "候选三", "price": 30,
             "market_cap": 100, "amount": 10, "change_pct": -2,
             "main_net_pct": -3, "main_net": -2e8, "is_st": False, "board": "深圳主板"},
        ])

        leaders = feed._rank_board_leaders({"300308", "000002", "000003"})

        self.assertEqual([row["code"] for row in leaders], ["300308", "000002"])
        self.assertEqual([row["role"] for row in leaders], ["龙头", "次龙头"])

    def test_hot_core_bypasses_fundamental_and_price_gates(self):
        screener = LongTermFundamentalScreener(data_feed=_HotCoreFeed())
        screener._technical_analysis = lambda code: {
            "technical_available": True, "technical_score": 70,
            "technical_reason": "趋势向上", "trend_confirmation": "确认",
        }
        screener._selection_news = [{
            "title": "中际旭创发布经营数据", "summary": "业绩增长",
            "source": "测试新闻", "time": "2026-08-10", "url": "https://example.com/news",
        }]
        boards = [{
            "name": "CPO概念", "type": "概念", "rotation_score": 85,
            "main_net_inflow": 20, "leaders": [
                {"code": "300308", "name": "中际旭创", "role": "龙头",
                 "leadership_score": 95, "price": 864},
                {"code": "000002", "name": "候选二", "role": "次龙头",
                 "leadership_score": 85, "price": 50},
            ],
        }]

        result = screener._build_hot_core_candidates(boards)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["code"], "300308")
        self.assertEqual(result[0]["leadership_role"], "龙头")
        self.assertEqual(result[0]["hot_board"], "CPO概念")
        self.assertEqual(result[0]["related_news"][0]["source"], "测试新闻")


if __name__ == "__main__":
    unittest.main()
