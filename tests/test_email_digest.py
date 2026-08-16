import json
import unittest
from unittest.mock import MagicMock, patch

from email_digest import build_email, send_email


def payload(observations=None):
    return {
        "subject": "2026-08-16 A股双评分午间观察",
        "generated_at": "2026-08-16 12:00:00",
        "analysis_window": "前一交易日完整盘面 + 当日09:30-11:30上午盘",
        "execution_window": "13:00-14:00复核使用",
        "market": {"up": 2000, "down": 2800, "limit_up": 40, "limit_down": 8},
        "rules": {"fundamental_min": 60, "technical_min": 60, "display_limit": 20},
        "technical_summary": {"metadata": {"signal_date": "2026-08-15"}, "oos_metrics": {"annual_return": 12.3, "max_drawdown": -8.2, "sharpe_ratio": 1.1}},
        "observations": observations or [],
    }


class EmailDigestTest(unittest.TestCase):
    def test_build_email_contains_only_score_observation(self):
        message = build_email(payload([{
            "rank": 1, "code": "000001", "name": "平安银行", "industry": "银行",
            "fundamental_score": 78, "technical_score": 72.5, "combined_score": 75.25,
        }]))
        plain = message.get_body(preferencelist=("plain",)).get_content()
        html = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("双评分午间观察", message["Subject"])
        self.assertIn("000001 平安银行", plain)
        self.assertIn("基本面78", plain)
        self.assertIn("不生成仓位、进场价、止盈止损或自动委托", plain)
        self.assertIn("没有交集时允许为空", html)
        self.assertIn("table-layout:fixed", html)
        self.assertEqual(html.count('width="7%"'), 2)
        self.assertEqual(html.count('width="22%"'), 2)
        self.assertEqual(html.count('width="23%"'), 2)
        self.assertEqual(html.count('width="16%"'), 6)

    def test_empty_intersection_is_explicit(self):
        message = build_email(payload())
        self.assertIn("今日没有股票同时达到两类评分阈值", message.get_body(preferencelist=("plain",)).get_content())

    @patch("email_digest.smtplib.SMTP_SSL")
    @patch.dict("os.environ", {"MAIL_USERNAME": "sender@qq.com", "MAIL_PASSWORD": "secret", "MAIL_TO": "a@test.com;b@test.com"}, clear=True)
    def test_smtp_supports_multiple_recipients(self, smtp_ssl):
        smtp = MagicMock()
        smtp_ssl.return_value.__enter__.return_value = smtp
        message = build_email(payload())
        send_email(message)
        self.assertEqual(message["To"], "a@test.com, b@test.com")
        smtp.send_message.assert_called_once_with(message)

    @patch("email_digest.urlopen")
    @patch.dict("os.environ", {"BREVO_API_KEY": "key", "BREVO_SENDER_EMAIL": "sender@qq.com", "MAIL_USERNAME": "sender@qq.com", "MAIL_TO": "a@test.com"}, clear=True)
    def test_brevo_transport(self, urlopen):
        response = MagicMock(status=201)
        urlopen.return_value.__enter__.return_value = response
        send_email(build_email(payload()))
        sent = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(sent["to"], [{"email": "a@test.com"}])


if __name__ == "__main__":
    unittest.main()
