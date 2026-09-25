import unittest
from send_holding_review import review_message


class HoldingReviewTest(unittest.TestCase):
    def test_review_is_dated_escaped_and_blocks_missing_data(self):
        with self.assertRaises(RuntimeError):
            review_message([], "2026-09-24")
        row = {"morning_ready": False}
        with self.assertRaises(RuntimeError):
            review_message([row], "2026-09-24")
        row.update(morning_ready=True, code="600183", name="<样本>",
                   sector_name="元件", action="持有观察", reason="已完成上午行情校验")
        message = review_message([row], "2026-09-24")
        self.assertIn("非今日交易建议", str(message["Subject"]))
        for kind in ("plain", "html"):
            self.assertIn("2026-09-24 11:30", message.get_body(preferencelist=(kind,)).get_content())
        self.assertIn("&lt;样本&gt;", message.get_body(preferencelist=("html",)).get_content())
