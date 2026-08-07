import unittest

from fundamental import LongTermFundamentalScreener


class _RotationFeed:
    def __init__(self):
        self.board = {
            "name": "半导体",
            "type": "行业",
            "flow_score": 80,
            "main_net_inflow": 20.0,
            "recent_main_net_inflow": 30.0,
        }

    def get_rotation_matches(self, candidate_codes, top_n=8):
        return {
            "boards": [self.board],
            "matches": {"000001": [self.board]},
        }

    @staticmethod
    def get_market_context():
        return {"market_stats": {}}

    @staticmethod
    def get_financial_news():
        return []


class _RotationAdvisor:
    is_configured = True

    @staticmethod
    def analyze_sector_rotation(context, news, boards):
        return {
            "available": True,
            "mode": "ai_assisted",
            "market_stage": "反转待确认",
            "boards": [{
                "name": "半导体",
                "state": "反转待确认",
                "rotation_score": 100,
                "confidence": "中",
                "reason": "资金与扩散度改善",
                "trigger": "连续流入",
                "invalidation": "资金转负",
            }],
        }


class FundamentalRotationTest(unittest.TestCase):
    def test_ai_rotation_score_is_bounded_overlay_on_rule_score(self):
        feed = _RotationFeed()
        screener = LongTermFundamentalScreener(data_feed=feed)
        ranked = [{
            "code": "000001",
            "composite_score": 70,
            "main_net_pct": 2,
        }]

        selected, boards, analysis = screener._select_recommendations(
            ranked, ai_advisor=_RotationAdvisor()
        )

        self.assertTrue(analysis["available"])
        self.assertEqual(boards[0]["rule_flow_score"], 80)
        self.assertEqual(boards[0]["ai_rotation_score"], 100)
        self.assertEqual(boards[0]["rotation_score"], 85)
        self.assertEqual(selected[0]["market_flow_score"], 79)
        self.assertIn("AI:反转待确认/中", selected[0]["matched_themes"][0])


if __name__ == "__main__":
    unittest.main()
