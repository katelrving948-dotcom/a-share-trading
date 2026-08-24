import unittest

from server import _public_push_payload


class AccountPrivacyTest(unittest.TestCase):
    def test_public_preview_removes_private_account_and_holding_actions(self):
        public = _public_push_payload({
            "account": {
                "equity": 50000, "available_cash": 20000,
                "holdings": [{"code": "000933", "quantity": 400, "cost_price": 25}],
                "holdings_value": 10000, "risk_profile": {"name": "普通"},
            },
            "weekly_plan": {
                "account": {"equity": 50000, "holdings": [{"code": "000933"}]},
                "holding_actions": [{"code": "000933", "action": "减仓"}],
            },
        })
        self.assertEqual(public["account"]["holdings_count"], 1)
        self.assertNotIn("equity", public["account"])
        self.assertNotIn("holdings", public["account"])
        self.assertEqual(public["weekly_plan"]["holding_actions"], [])
        self.assertNotIn("holdings", public["weekly_plan"]["account"])


if __name__ == "__main__":
    unittest.main()
