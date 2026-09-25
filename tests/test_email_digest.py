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
        "rules": {"fundamental_min": 60, "technical_min": 60, "display_limit": 20, "selection_weights": {"fundamental": 0.4, "technical": 0.4, "board": 0.1, "morning_fund": 0.1}},
        "technical_summary": {
            "metadata": {"signal_date": "2026-08-15"},
            "oos_metrics": {"annual_return": 12.3, "max_drawdown": -8.2, "sharpe_ratio": 1.1, "trading_days": 252},
            "latest_validation": {"status": "validated", "signal_date": "2026-08-14", "validation_date": "2026-08-15", "hit_rate": 60, "average_return": 0.5, "excess_return": 0.2, "message": "已验证"},
            "optimization_log_entry": {
                "actions": ["重新运行45组参数的滚动训练与样本外回测"],
                "parameter_changes": [
                    {"part": "momentum_window", "before": 20, "after": 60},
                    {"part": "selection_weights.fundamental", "before": 0.4, "after": 0.45},
                ],
                "metric_changes": [{"metric": "sharpe_ratio", "before": 0.8, "after": 1.1, "delta": 0.3}],
                "guardrail": "只在预设参数网格内选择",
            },
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
    @patch("email_digest.market_closed_reason", return_value="")
    @patch.dict("os.environ", {"EMAIL_PREVIEW_ONLY": "true"}, clear=True)
    @patch("email_digest.Path.write_text")
    @patch("email_digest.freeze_weekly_plan")
    @patch("email_digest.save_selection_snapshot")
    @patch("email_digest.send_email")
    @patch("email_digest.build_push_payload")
    def test_preview_never_sends_and_missing_boards_block_resend(self, build, send, save, freeze, write, closed):
        from email_digest import main
        data = payload()
        data["technical_summary"]["factor_count"] = 1
        data["market"]["sector_flow"] = [{"name": "电子"}]
        build.return_value = data
        main()
        send.assert_not_called()
        save.assert_not_called()
        freeze.assert_not_called()
        data["holding_actions"] = [{"morning_ready": False}]
        with self.assertRaisesRegex(RuntimeError, "持仓上午行情"):
            main()
        data["holding_actions"] = []
        data["rotation_boards"][0]["member_count"] = 0
        with self.assertRaisesRegex(RuntimeError, "板块数据"):
            main()
        data["rotation_boards"] = []
        with self.assertRaisesRegex(RuntimeError, "板块数据"):
            main()
        send.assert_not_called()

    @patch("email_digest.market_closed_reason", return_value="中秋节休市")
    @patch("email_digest.build_push_payload")
    @patch("email_digest.send_email")
    def test_holiday_skips_all_generation_and_sending(self, send, build, closed):
        from email_digest import main
        main()
        build.assert_not_called()
        send.assert_not_called()

    def test_derived_board_values_are_labeled_with_morning_time(self):
        data = payload()
        data["rotation_boards"][0].update(main_net_estimated=True, as_of="2026-09-24 11:30",
                                          source="新浪分时约值")
        message = build_email(data)
        for kind in ("plain", "html"):
            body = message.get_body(preferencelist=(kind,)).get_content()
            self.assertIn("约12.00亿", body)
            self.assertIn("2026-09-24 11:30", body)

    def test_missing_board_data_is_not_reported_as_zero_or_no_candidates(self):
        from research_core import _capital_strength
        data = payload()
        data.update(capital_strength=_capital_strength({}, []), rotation_boards=[], hot_core_candidates=[])
        message = build_email(data)
        for kind in ("plain", "html"):
            body = message.get_body(preferencelist=(kind,)).get_content()
            self.assertIn("板块资金数据缺失，无法判断", body)
            self.assertIn("板块数据缺失，暂无法生成龙头观察名单", body)
            self.assertNotIn("合计 0.00", body)
            self.assertNotIn("弱或分化", body)

    def test_weekly_plan_and_account_gate_are_prominent(self):
        data = payload()
        data["account"] = {
            "equity": 50000, "last_week_pnl": -3500, "last_week_return_pct": -7,
            "can_open_new": True, "block_reasons": [],
            "risk_profile": {"name": "恢复期", "max_total_pct": 0.3},
        }
        data["weekly_plan"] = {
            "plan_id": "2026-W35", "selection_policy": "周内只撤销，不换排行",
            "event_scenarios": [{"name": "基准情景", "summary": "等待A股确认", "triggers": ["板块趋势保持"]}],
            "selections": [{
                "role": "主选", "code": "000001", "name": "周度样本", "status": "可执行", "weekly_score": 82,
                "market_cap": 25,
                "holding_plan": {"label": "3-10个交易日"},
                "entry_gate": {"passed": False, "reason": "等待回调"},
                "weekly_trend": {"entry_zone": {"low": 10, "high": 10.2}, "max_chase_price": 10.5, "stop_price": 9.6, "take_profit": [{"price": 11}, {"price": 12}]},
                "position_plan": {"quantity": 300, "estimated_value": 3030, "planned_loss": 180, "reasons": []},
            }],
            "holding_actions": [],
        }
        message = build_email(data)
        plain = message.get_body(preferencelist=("plain",)).get_content()
        html = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("账户风险闸门", plain)
        self.assertIn("本周固定名单", plain)
        self.assertIn("000001 周度样本", plain)
        self.assertIn("恢复期", html)
        self.assertIn("国际事件三情景", html)
        self.assertIn("目标上限", plain)
        self.assertIn("3-10个交易日", plain)
        self.assertIn("3-10个交易日", html)
        self.assertIn("等待回调", html)
        self.assertIn("市值25.0亿元", plain)

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
        self.assertIn("量化因子仅独立研究，不参与选股", html)
        self.assertIn("外盘、美股与地缘事件影响", html)
        self.assertIn("每日资金强度、板块效应与龙头", html)
        self.assertIn("量化次日验证与每日优化日志", html)
        self.assertIn("动量计算窗口：20 → 60", plain)
        self.assertIn("研究试验-基本面权重：40% → 45%", plain)
        self.assertIn("样本外夏普比率：0.8 → 1.1", html)
        self.assertIn("量化因子不参与实际选股", plain)
        self.assertIn("000002 板块核心", plain)
        self.assertIn("进场10.10-10.30", plain)
        self.assertIn("止损9.80-9.90", html)
        self.assertIn("止盈一10.90-11.10", html)
        self.assertIn("table-layout:fixed", html)
        self.assertEqual(html.count('width="7%"'), 2)
        self.assertEqual(html.count('width="22%"'), 2)
        self.assertEqual(html.count('width="23%"'), 2)
        self.assertEqual(html.count('width="16%"'), 6)

    def test_email_includes_confirmed_holding_action(self):
        data = payload()
        data["account"] = {
            "equity": 50000, "can_open_new": True, "block_reasons": [],
            "risk_profile": {"name": "普通", "max_total_pct": 0.6},
        }
        data["weekly_plan"] = {
            "selection_policy": "周内只撤销",
            "selections": [],
            "event_scenarios": [],
            "holding_actions": [{
                "code": "000933", "name": "样本", "quantity": 400,
                "available_quantity": 400, "cost_price": 25.9803,
                "reference_price": 26.08, "pnl_pct": 0.38,
                "action": "持有", "sell_quantity": 0,
                "reason": "周度趋势保持", "stop_price": 24.8,
            }],
        }
        message = build_email(data)
        plain = message.get_body(preferencelist=("plain",)).get_content()
        html = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("午间持仓建议", plain)
        self.assertIn("000933 样本", plain)
        self.assertIn("周度趋势保持", html)

    def test_empty_intersection_is_explicit(self):
        message = build_email(payload())
        self.assertIn("今日没有股票达到基本面、板块和盘中条件", message.get_body(preferencelist=("plain",)).get_content())

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
