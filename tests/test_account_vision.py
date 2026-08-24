import json
import unittest
from unittest.mock import MagicMock, patch

from account_vision import extract_account_screenshot


class AccountVisionTest(unittest.TestCase):
    @patch("account_vision.urlopen")
    @patch.dict("account_vision.os.environ", {
        "DASHSCOPE_API_KEY": "test-key",
        "DASHSCOPE_VISION_MODEL": "qwen3-vl-plus",
    }, clear=True)
    def test_screenshot_returns_unconfirmed_structured_draft(self, urlopen):
        model_output = {
            "equity": 45682.77,
            "available_cash": 35250.77,
            "as_of": "07:58",
            "screen_warning": "展示数据可能不准确",
            "holdings": [{
                "code": "000933", "name": "样本", "quantity": 400,
                "available_quantity": 400, "cost_price": 25.9803,
                "current_price": 26.08, "market_value": 10432,
                "confidence": "高", "review_note": None,
            }],
        }
        response = MagicMock()
        response.read.return_value = json.dumps({
            "choices": [{"message": {"content": json.dumps(model_output)}}]
        }).encode("utf-8")
        urlopen.return_value.__enter__.return_value = response

        draft = extract_account_screenshot("data:image/jpeg;base64,YWJj")
        request_payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertFalse(draft["confirmed"])
        self.assertEqual(draft["holdings"][0]["code"], "000933")
        self.assertEqual(draft["source"], "screenshot_bailian_draft")
        self.assertEqual(request_payload["model"], "qwen3-vl-plus")
        self.assertFalse(request_payload["enable_thinking"])
        self.assertEqual(request_payload["response_format"]["type"], "json_schema")
        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        )

    @patch.dict("account_vision.os.environ", {}, clear=True)
    def test_missing_dashscope_key_keeps_manual_entry_available(self):
        with self.assertRaisesRegex(RuntimeError, "DASHSCOPE_API_KEY"):
            extract_account_screenshot("data:image/jpeg;base64,YWJj")


if __name__ == "__main__":
    unittest.main()
