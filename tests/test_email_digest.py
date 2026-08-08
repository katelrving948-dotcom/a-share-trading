import unittest
import json
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
                "opening_plan": {
                    "actionable": True,
                    "status": "强势回踩",
                    "entry_zone": {"low": 10.40, "high": 10.50},
                    "breakout_trigger": 10.70,
                    "stop_zone": {"low": 10.05, "high": 10.10},
                },
            }],
            {"scan_scope": "诊断限制 500 只"},
            datetime(2026, 7, 22, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )

        self.assertIn("2026-07-22", message["Subject"])
        self.assertIn("平安银行(000001)", message.get_body(preferencelist=("plain",)).get_content())
        html_body = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("银行(行业, 近5日净流入+2.30亿)", html_body)
        self.assertIn("进 10.40–10.50", html_body)
        self.assertIn("止 10.05–10.10", html_body)
        self.assertIn("不构成投资建议", html_body)

    def test_build_email_handles_empty_result(self):
        message = build_email(
            [],
            {},
            datetime(2026, 7, 22, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
        self.assertIn("未返回有效候选", message.get_body(preferencelist=("plain",)).get_content())

    def test_build_email_contains_fund_flow_and_sector_rotation(self):
        summary = {
            "rotation_boards": [{
                "name": "半导体",
                "type": "行业",
                "change_pct": 2.1,
                "main_net_inflow": 20.5,
                "recent_main_net_inflow": 30.0,
                "rotation_score": 85,
                "ai_state": "反转待确认",
                "ai_confidence": "中",
                "ai_trigger": "连续流入",
                "ai_invalidation": "资金转负",
            }],
            "rotation_analysis": {
                "available": True,
                "market_stage": "潜在反转",
                "market_stage_reason": "市场宽度改善",
                "short_term_outlook": "关注资金延续",
                "medium_term_outlook": "等待政策验证",
                "rotation_path": [{
                    "from": "医药", "to": "半导体",
                    "driver": "政策预期", "confidence": "中",
                }],
                "risks": ["单日信号可能失真"],
            },
        }

        message = build_email(
            [], summary,
            datetime(2026, 8, 8, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )

        plain_body = message.get_body(preferencelist=("plain",)).get_content()
        html_body = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("资金流动与板块轮动", plain_body)
        self.assertIn("医药 → 半导体", plain_body)
        self.assertIn("当日 / 近5日主力净流入", html_body)
        self.assertIn("20.50亿", html_body)
        self.assertIn("反转待确认 / 中", html_body)

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

    @patch("email_digest.urlopen")
    @patch("email_digest.smtplib.SMTP_SSL")
    @patch.dict("os.environ", {
        "BREVO_API_KEY": "api-key",
        "BREVO_SENDER_EMAIL": "sender@qq.com",
        "MAIL_USERNAME": "sender@qq.com",
        "MAIL_TO": "first@example.com,second@example.com",
    }, clear=True)
    def test_send_email_uses_brevo_api_when_configured(self, smtp_ssl, urlopen):
        response = MagicMock()
        response.status = 201
        urlopen.return_value.__enter__.return_value = response
        message = build_email(
            [], {},
            datetime(2026, 7, 22, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )

        send_email(message)

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://api.brevo.com/v3/smtp/email")
        self.assertEqual(payload["sender"]["email"], "sender@qq.com")
        self.assertEqual(
            [recipient["email"] for recipient in payload["to"]],
            ["first@example.com", "second@example.com"],
        )
        smtp_ssl.assert_not_called()


if __name__ == "__main__":
    unittest.main()
