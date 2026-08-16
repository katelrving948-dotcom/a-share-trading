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
        "technical_summary": {
            "metadata": {"signal_date": "2026-08-15"},
            "oos_metrics": {"annual_return": 12.3, "max_drawdown": -8.2, "sharpe_ratio": 1.1, "trading_days": 252},
            "latest_validation": {"status": "validated", "signal_date": "2026-08-14", "validation_date": "2026-08-15", "hit_rate": 60, "average_return": 0.5, "excess_return": 0.2, "message": "已验证"},
            "optimization_log_entry": {"actions": ["动量窗口20调整为60"], "guardrail": "只在预设参数网格内选择"},
        },
        "external_market": {"coverage": "1/8项外盘行情，1类事件信号", "markets": [{"name": "纳斯达克100", "change_pct": 1.2, "as_of": "2026-08-15"}], "events": [{"name": "地缘政治", "impact_summary": "等待A股资金确认"}]},
        "capital_strength": {"label": "强", "strong_board_count": 1, "top_three_main_net_inflow": 20},
        "quant_model_gate": {"passed": True, "reason": "样本外总闸门通过"},
        "rotation_boards": [{"rank": 1, "name": "半导体", "type": "行业", "main_net_inflow": 12, "rotation_score": 72, "effect": "资金流入且上涨扩散", "leaders": [{"name": "测试龙头", "leadership_role": "龙头"}]}],
        "hot_core_candidates": [{
            "code": "000002", "name": "板块核心", "board_name": "半导体", "leadership_role": "龙头",
            "board_strength_score": 72, "fundamental_score": 68, "technical_score": 75,
            "trade_decision": {"status": "等待触发"},
            "morning_plan": {
                "levels_available": True, "status": "等待回踩或突破确认",
                "entry_zone": {"low": 10.1, "high": 10.3}, "breakout_trigger": 10.5,
                "max_chase_price": 10.7, "stop_zone": {"low": 9.8, "high": 9.9},
                "take_profit_zones": [{"low": 10.9, "high": 11.1}, {"low": 11.4, "high": 11.7}],
            },
        }],
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
        self.assertIn("不自动委托；排名不等于买点", plain)
        self.assertIn("没有交集时允许为空", html)
        self.assertIn("外盘、美股与地缘事件影响", html)
        self.assertIn("每日资金强度、板块效应与龙头", html)
        self.assertIn("量化次日验证与每日优化日志", html)
        self.assertIn("000002 板块核心", plain)
        self.assertIn("进场10.10-10.30", plain)
        self.assertIn("止损9.80-9.90", html)
        self.assertIn("止盈一10.90-11.10", html)
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
