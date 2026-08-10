import unittest

import pandas as pd

from data_feed import DataFeed
from fundamental import LongTermFundamentalScreener


class _Response:
    @staticmethod
    def json():
        symbols = (
            ("DJIA", "道琼斯", 0.3), ("SPX", "标普500", 0.6),
            ("NDX", "纳斯达克", 1.5), ("HXC", "中国金龙", 1.0),
            ("HSI", "恒生指数", -0.4), ("CL00Y", "原油", 2.0),
            ("GC00Y", "黄金", 1.2), ("HG00Y", "铜", -1.0),
        )
        return {
            "data": {"diff": [
                {
                    "f12": symbol, "f14": name, "f2": 100,
                    "f3": change, "f4": change, "f18": 99,
                    "f124": 1786200000,
                }
                for symbol, name, change in symbols
            ]}
        }


class _NewsResponse:
    def __init__(self, column):
        self.column = column

    def __bool__(self):
        return True

    def json(self):
        title_by_column = {
            "102": "A股综合快讯",
            "105": "纳斯达克科技股收涨",
            "106": "伊朗批准霍尔木兹海峡安全纲要",
            "107": "美元指数回落",
            "108": "美债收益率下行",
        }
        title = title_by_column[self.column]
        return {"data": {"fastNewsList": [{
            "code": f"20260810{self.column}",
            "title": title,
            "summary": title,
            "showTime": "2026-08-10 18:30:00",
        }]}}


class ExternalMarketRotationTest(unittest.TestCase):
    def _feed(self):
        feed = DataFeed.__new__(DataFeed)
        feed._external_market_cache = None
        feed._external_market_cache_time = 0
        feed._request_eastmoney = lambda *args, **kwargs: (_Response(), "东方财富延时行情")
        feed._set_source_state = lambda *args, **kwargs: None
        return feed

    def test_external_snapshot_and_event_map_to_boards(self):
        feed = self._feed()
        context = feed.get_external_market_context(news=[{
            "title": "霍尔木兹海峡通行受阻，国际油价上涨",
            "summary": "中东地缘冲突升温",
            "time": "08:10", "source": "东方财富",
        }])

        self.assertTrue(context["available"])
        self.assertEqual(len(context["markets"]), 8)
        self.assertTrue(any(item["symbol"] == "HXC" for item in context["markets"]))
        self.assertEqual(context["events"][0]["id"], "geopolitics")
        self.assertGreater(feed.score_board_external_impact("石油行业", context)["score"], 70)
        self.assertLess(feed.score_board_external_impact("航空机场", context)["score"], 50)
        self.assertGreater(feed.score_board_external_impact("半导体", context)["score"], 50)

    def test_external_news_uses_live_columns_and_keeps_source_links(self):
        feed = DataFeed.__new__(DataFeed)
        feed._request = lambda url, params: _NewsResponse(params["fastColumn"])

        general = feed.get_financial_news(10)
        external = feed.get_external_news(20)

        self.assertEqual(general[0]["title"], "A股综合快讯")
        self.assertEqual(len(external), 4)
        self.assertTrue(any("霍尔木兹" in item["title"] for item in external))
        self.assertTrue(all(item["url"].startswith("https://finance.eastmoney.com/a/") for item in external))
        self.assertEqual(
            {item["category"] for item in external},
            {"全球股市", "商品地缘", "外汇宏观", "债券利率"},
        )

    def test_easing_phrase_is_not_misread_as_tightening(self):
        events = DataFeed._extract_external_events([{
            "title": "非农意外转负，加息预期降温",
            "summary": "市场对美元流动性收紧的担忧下降",
            "time": "2026-08-10 18:00",
            "source": "东方财富·债券利率",
        }])

        self.assertIn("fed_easing", [event["id"] for event in events])
        self.assertNotIn("fed_tightening", [event["id"] for event in events])

    def test_external_candidate_enters_pool_before_fund_flow_leads(self):
        feed = self._feed()
        rows = pd.DataFrame([
            {"code": "BK1", "name": "银行", "change_pct": 1, "main_net_inflow": 20},
            {"code": "BK2", "name": "半导体", "change_pct": 0.5, "main_net_inflow": 1},
        ])
        feed.get_sector_fund_flow = lambda *args, **kwargs: rows
        feed.get_concept_fund_flow = lambda *args, **kwargs: pd.DataFrame()
        feed.get_board_flow_history = lambda *args, **kwargs: {
            "recent_main_net_inflow": None, "positive_days": None, "days": 0,
        }
        feed.get_board_constituents = lambda code: {"000001"} if code == "BK2" else set()
        context = {
            "available": True,
            "markets": [{"symbol": "NDX", "name": "纳斯达克100", "change_pct": 2.0}],
            "events": [],
        }

        result = feed.get_rotation_matches(
            ["000001"], top_n=1, external_context=context
        )

        self.assertIn("半导体", [board["name"] for board in result["boards"]])
        self.assertEqual(result["matches"]["000001"][0]["name"], "半导体")


class _ExternalRotationFeed:
    @staticmethod
    def get_financial_news():
        return []

    @staticmethod
    def get_external_market_context(news=None):
        return {
            "available": True, "source": "测试外盘", "coverage": "8/8",
            "markets": [{"symbol": "NDX", "name": "纳斯达克100", "change_pct": 2.0}],
            "events": [], "limitations": ["测试限制"],
        }

    @staticmethod
    def get_market_context():
        return {
            "market_stats": {"up": 3000, "down": 1800, "avg_change": 0.6},
        }

    @staticmethod
    def get_rotation_matches(candidate_codes, top_n=8, external_context=None):
        board = {
            "name": "半导体", "type": "行业", "flow_score": 80,
            "main_net_inflow": 20.0, "recent_main_net_inflow": 30.0,
            "external_score": 100, "external_confidence": "高",
            "external_signal_count": 1, "external_reasons": ["纳斯达克100+2.00%"],
        }
        return {"boards": [board], "matches": {"000001": [board]}}


class FundamentalExternalRotationTest(unittest.TestCase):
    def test_external_score_changes_rule_rotation_without_ai(self):
        screener = LongTermFundamentalScreener(data_feed=_ExternalRotationFeed())
        selected, boards, analysis = screener._select_recommendations([{
            "code": "000001", "composite_score": 70, "main_net_pct": 2,
        }])

        self.assertFalse(analysis["available"])
        self.assertTrue(analysis["external_available"])
        self.assertEqual(analysis["mode"], "rule_external")
        self.assertEqual(boards[0]["rule_flow_score"], 80)
        self.assertEqual(boards[0]["rule_external_score"], 84)
        self.assertEqual(boards[0]["rotation_score"], 84)
        self.assertIn("外盘/事件方向支持", analysis["short_term_outlook"])
        self.assertGreater(selected[0]["market_flow_score"], 75)


if __name__ == "__main__":
    unittest.main()
