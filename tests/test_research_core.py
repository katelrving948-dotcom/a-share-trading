import unittest
from unittest.mock import patch

from fundamental import FundamentalScorer
from research_core import score_intersection


class ResearchCoreTest(unittest.TestCase):
    def test_fundamental_score_has_four_transparent_dimensions(self):
        row = FundamentalScorer._evaluate({
            "code": "1", "name": "样本", "available": True,
            "annualized_roe": 15, "roe": 15, "gross_margin": 30,
            "revenue_growth": 12, "profit_growth": 18,
            "eps": 1, "operating_cf_per_share": 1.2, "pe": 18, "pb": 2.5,
        })
        self.assertEqual(row["code"], "000001")
        self.assertEqual(
            set(("quality_score", "growth_score", "valuation_score", "cashflow_score"))
            - set(row),
            set(),
        )
        self.assertGreaterEqual(row["fundamental_score"], 0)
        self.assertLessEqual(row["fundamental_score"], 100)

    @patch.dict("os.environ", {"PUSH_FUNDAMENTAL_MIN": "60", "PUSH_TECHNICAL_MIN": "60", "PUSH_DISPLAY_LIMIT": "20"})
    def test_intersection_is_not_fixed_to_ten_and_requires_both_scores(self):
        fundamental = {"rows": [
            {"code": "000001", "name": "双达标", "fundamental_score": 75},
            {"code": "000002", "name": "技术不达标", "fundamental_score": 80},
            {"code": "000003", "name": "基本面不达标", "fundamental_score": 59},
        ]}
        technical = {"rows": [
            {"code": "000001", "technical_score": 70},
            {"code": "000002", "technical_score": 50},
            {"code": "000003", "technical_score": 90},
        ]}
        result = score_intersection(fundamental, technical)
        self.assertEqual([row["code"] for row in result], ["000001"])
        self.assertEqual(result[0]["combined_score"], 72.5)


if __name__ == "__main__":
    unittest.main()
