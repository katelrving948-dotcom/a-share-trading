import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from email_digest import build_email, send_email


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

    @patch("email_digest.smtplib.SMTP_SSL")
    @patch.dict("os.environ", {
        "MAIL_USERNAME": "sender@gmail.com",
        "MAIL_PASSWORD": "app-password",
        "MAIL_TO": "first@example.com, second@example.com;third@example.com",
    }, clear=True)
    def test_send_email_supports_multiple_recipients(self, smtp_ssl):
        smtp = MagicMock()
        smtp_ssl.return_value.__enter__.return_value = smtp
        message = build_email(
            [], {},
            datetime(2026, 7, 22, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )

        send_email(message)

        self.assertEqual(
            message["To"],
            "first@example.com, second@example.com, third@example.com",
        )
        smtp.login.assert_called_once_with("sender@gmail.com", "app-password")
        smtp.send_message.assert_called_once_with(message)


if __name__ == "__main__":
    unittest.main()
