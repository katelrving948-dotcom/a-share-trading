import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from email_digest import build_email


class EmailDigestTest(unittest.TestCase):
    def test_build_email_contains_ranked_candidate_and_warning(self):
        message = build_email(
            [{
                "code": "000001",
                "name": "平安银行",
                "price": 10.56,
                "selection_score": 82,
                "fundamental_score": 79,
                "technical_score": 88,
                "matched_themes": ["银行(行业, 近5日净流入+2.30亿)"],
                "risk": "估值指标偏高",
            }],
            {"scan_scope": "诊断限制 500 只"},
            datetime(2026, 7, 22, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )

        self.assertIn("2026-07-22", message["Subject"])
        self.assertIn("平安银行(000001)", message.get_body(preferencelist=("plain",)).get_content())
        html_body = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("银行(行业, 近5日净流入+2.30亿)", html_body)
        self.assertIn("不构成投资建议", html_body)

    def test_build_email_handles_empty_result(self):
        message = build_email(
            [],
            {},
            datetime(2026, 7, 22, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
        self.assertIn("未返回有效候选", message.get_body(preferencelist=("plain",)).get_content())


if __name__ == "__main__":
    unittest.main()
