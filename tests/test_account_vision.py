import json
import unittest
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from account_vision import complete_missing_codes, extract_account_screenshot


class AccountVisionTest(unittest.TestCase):
    def test_public_preview_cannot_clear_private_account_fields(self):
        html = (Path(__file__).resolve().parents[1] / "templates/index.html").read_text(encoding="utf-8")
        function = re.search(r"async function loadPush\(.*?(?=\ndocument.getElementById\('pushReload'\))", html, re.S).group()
        script = """
const assert=require('node:assert/strict');
const fields={accountEquity:{value:'51857.79'},lastWeekPnl:{value:'100'},currentWeekPnl:{value:'200'}};
const document={getElementById:id=>fields[id]||(fields[id]={})};
const api={get:async()=>({account:{},rules:{}})};
const metric=()=>'',num=()=>'',esc=x=>x;
""" + function + """
(async()=>{await loadPush();
assert.equal(fields.accountEquity.value,'51857.79');
assert.equal(fields.lastWeekPnl.value,'100');
assert.equal(fields.currentWeekPnl.value,'200');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
        result = subprocess.run(["node", "-"], input=script, encoding="utf-8", capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    @patch("data_feed.DataFeed")
    def test_two_incorrect_identical_codes_are_corrected_independently(self, feed):
        feed.return_value.get_stock_list.return_value.to_dict.return_value = [
            {"name": "生益科技", "code": "600183"},
            {"name": "千金药业", "code": "600479"},
        ]
        rows = [{"name": "生益科技", "code": "600000", "quantity": 100},
                {"name": "千金药业", "code": "600000", "quantity": 900}]
        complete_missing_codes({"holdings": rows})
        self.assertEqual([r["code"] for r in rows], ["600183", "600479"])
        self.assertEqual([r["quantity"] for r in rows], [100, 900])
        self.assertTrue(all("与名称不符" in r["code_match_note"] for r in rows))

    @patch("data_feed.DataFeed")
    def test_unverified_nonempty_code_is_cleared_on_outage(self, feed):
        feed.return_value.get_stock_list.side_effect = RuntimeError("offline")
        row = {"name": "千金药业", "code": "600000"}
        complete_missing_codes({"holdings": [row]})
        self.assertIsNone(row["code"])
        self.assertIn("未通过核验", row["code_match_note"])

    def test_frontend_merge_preserves_conflicting_names(self):
        html = (Path(__file__).resolve().parents[1] / "templates/index.html").read_text(encoding="utf-8")
        function = re.search(r"function mergeHoldingDrafts\(.*?(?=\nfunction )", html, re.S).group()
        script = function + """
const assert=require('node:assert/strict');
const rows=[{name:'生益科技',code:'600000',quantity:100},{name:'千金药业',code:'600000',quantity:900}];
const result=mergeHoldingDrafts([],rows);
assert.equal(result.length,2);
assert.deepEqual(result.map(x=>x.code),['','']);
assert.deepEqual(result.map(x=>x.quantity),[100,900]);
assert.equal(mergeHoldingDrafts([],rows.map(x=>({...x,code:null}))).length,2);
const same=mergeHoldingDrafts([{name:'生益科技',code:'600183',quantity:100}], [{name:'生益科技',code:'600183',quantity:200}]);
assert.equal(same.length,1);assert.equal(same[0].quantity,200);
"""
        result = subprocess.run(["node", "-"], input=script, encoding="utf-8", capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    @patch("data_feed.DataFeed")
    def test_missing_codes_match_unique_exact_names_only(self, feed):
        feed.return_value.get_stock_list.return_value.to_dict.return_value = [
            {"name": "生益科技", "code": "600183"},
            {"name": "千金药业", "code": "600479"},
            {"name": "重名", "code": "000001"},
            {"name": "重名", "code": "000002"},
        ]
        rows = [{"name": name, "code": None} for name in
                [" 生益科技 ", "千金药业", "生益", "重名"]]
        rows.append({"name": "生益科技", "code": "123456"})
        draft = complete_missing_codes({"holdings": rows, "confirmed": False})
        self.assertEqual([row["code"] for row in rows],
                         ["600183", "600479", None, None, "600183"])
        self.assertIn("多个", rows[3]["code_match_note"])
        self.assertIn("未找到", rows[2]["code_match_note"])
        self.assertFalse(draft["confirmed"])

    @patch("data_feed.DataFeed")
    def test_lookup_failure_preserves_draft(self, feed):
        feed.return_value.get_stock_list.side_effect = RuntimeError("offline")
        row = {"name": "生益科技", "code": None, "quantity": 100}
        draft = {"holdings": [row]}
        self.assertIs(complete_missing_codes(draft), draft)
        self.assertIsNone(row["code"])
        self.assertEqual(row["quantity"], 100)
        self.assertIn("暂不可用", row["code_match_note"])

    @patch("data_feed.DataFeed")
    @patch("account_vision.urlopen")
    @patch.dict("account_vision.os.environ", {
        "DASHSCOPE_API_KEY": "test-key",
        "DASHSCOPE_VISION_MODEL": "qwen3-vl-plus",
    }, clear=True)
    def test_screenshot_returns_unconfirmed_structured_draft(self, urlopen, feed):
        feed.return_value.get_stock_list.return_value.to_dict.return_value = [{"name": "样本", "code": "000933"}]
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
