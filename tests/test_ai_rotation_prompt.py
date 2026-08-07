import json
import unittest

from ai_advisor import AIAdvisor


class AIRotationPromptTest(unittest.TestCase):
    @staticmethod
    def _context():
        return {
            "market_stats": {
                "total": 5000,
                "up": 3200,
                "down": 1700,
                "limit_up": 60,
                "limit_down": 5,
                "avg_change": 0.8,
            },
            "top_gainers": [
                {"code": "000001", "name": "样本股", "change_pct": 5.2, "board": "半导体"},
            ],
            "top_losers": [
                {"code": "000002", "name": "回落股", "change_pct": -4.1, "board": "医药"},
            ],
            "hot_concepts": [
                {
                    "name": "先进封装",
                    "change_pct": 2.5,
                    "main_net_inflow": 12.3,
                    "main_net_pct": 4.6,
                },
            ],
            "sector_flow": [
                {
                    "name": "半导体",
                    "change_pct": 2.1,
                    "main_net_inflow": 20.5,
                    "main_net_pct": 3.8,
                    "rise_count": 80,
                    "fall_count": 12,
                },
            ],
            "sector_outflow": [
                {
                    "name": "医药",
                    "change_pct": -1.6,
                    "main_net_inflow": -18.2,
                    "main_net_pct": -3.2,
                    "rise_count": 15,
                    "fall_count": 70,
                },
            ],
            "concept_outflow": [],
        }

    def test_prompt_contains_rotation_evidence_and_confirmation_rules(self):
        advisor = AIAdvisor.__new__(AIAdvisor)
        prompt = advisor._build_market_prompt(
            self._context(), [{"time": "10:00", "title": "政策样本"}]
        )

        self.assertIn("净流入占比+3.80%", prompt)
        self.assertIn("上涨/下跌家数80/12", prompt)
        self.assertIn("行业板块主力净流出", prompt)
        self.assertIn("医药: 涨幅-1.60%", prompt)
        self.assertIn("未来1-3个交易日和3-10个交易日", prompt)
        self.assertIn("待后续交易日确认", prompt)
        self.assertIn("事实、推断和待验证事项", prompt)

    def test_structured_rotation_filters_unknown_boards_and_clamps_score(self):
        advisor = AIAdvisor.__new__(AIAdvisor)
        advisor.api_key = "test-key"
        payload = {
            "market_stage": "潜在反转",
            "market_stage_reason": "市场宽度改善",
            "short_term_outlook": "关注资金延续",
            "medium_term_outlook": "等待政策验证",
            "rotation_path": [
                {"from": "医药", "to": "半导体", "driver": "政策预期", "confidence": "中"},
                {"from": "虚构板块", "to": "半导体", "driver": "无", "confidence": "高"},
            ],
            "boards": [
                {
                    "name": "半导体", "state": "反转待确认", "rotation_score": 130,
                    "confidence": "中", "horizon": "1-3日", "reason": "资金扩散",
                    "trigger": "连续流入", "invalidation": "资金转负",
                },
                {"name": "虚构板块", "state": "延续", "rotation_score": 99},
            ],
            "risks": ["单日信号可能失真"],
        }
        advisor._call_ai = lambda *args, **kwargs: json.dumps(payload, ensure_ascii=False)
        boards = [
            {
                "name": "半导体", "type": "行业", "change_pct": 2.1,
                "main_net_inflow": 20.5, "flow_score": 80,
                "recent_main_net_inflow": 30.0, "positive_days": 4, "history_days": 5,
            },
            {
                "name": "医药", "type": "行业", "change_pct": -1.0,
                "main_net_inflow": -5.0, "flow_score": 40,
                "recent_main_net_inflow": -8.0, "positive_days": 1, "history_days": 5,
            },
        ]

        result = advisor.analyze_sector_rotation(
            context=self._context(), news=[], boards=boards
        )

        self.assertTrue(result["available"])
        self.assertEqual(result["boards"], [{
            "name": "半导体", "state": "反转待确认", "rotation_score": 100,
            "confidence": "中", "horizon": "1-3日", "reason": "资金扩散",
            "trigger": "连续流入", "invalidation": "资金转负",
        }])
        self.assertEqual(len(result["rotation_path"]), 1)


if __name__ == "__main__":
    unittest.main()
