import json
import tempfile
import threading
import unittest
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

import news_snapshot as news
import research_core
import server


def headline(title, stamp="2026-10-04 12:00:00", url="https://finance.eastmoney.com/a/1.html"):
    return {"title": title, "time": stamp, "source": "测试来源", "summary": title, "url": url}


def quotes(day="2026-10-02", hour="16:05:00"):
    return [{"symbol": symbol, "name": name, "price": 100, "change_pct": 1,
             "as_of": day + " " + hour} for symbol, name in news.US_INDICES]


class NewsSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 4, 14, tzinfo=news.SHANGHAI)
        self.feed = Mock()
        self.feed.get_financial_news.return_value = [headline("人民银行发布货币政策报告")]
        self.feed.get_external_news.return_value = [headline("俄乌停火谈判进展", url="https://example.com/geo")]

    def test_categories_deduplicate_exclude_old_future_and_foreign_finance(self):
        rows = [headline("人民银行降准"), headline("人民银行降准"),
                headline("美国银行与美联储", url="https://example.com/us"),
                headline("韩国银行用户信息泄露", url="https://example.com/kr"),
                headline("巴西国内总统选举", url="https://example.com/br"),
                headline("国内煤电项目开工", url="https://example.com/energy"),
                headline("中东停火谈判", url="https://example.com/geo"),
                headline("俄乌旧消息", "2026-09-01 12:00:00", "https://example.com/old"),
                headline("未来停火消息", "2026-10-05 12:00:00", "https://example.com/future"),
                headline("乱码\ufffd战争", url="https://example.com/bad"),
                headline("没有时间的战争", "", "https://example.com/no-time")]
        result = news.classify_news(rows, self.now)
        self.assertEqual([row["title"] for row in result["domestic"]], ["人民银行降准"])
        self.assertEqual([row["title"] for row in result["geopolitics"]], ["中东停火谈判"])
        self.assertEqual(len(result["us"]), 1)

    def test_failure_retains_successful_items_and_success_time(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(news, "get_us_quotes", return_value=quotes()):
            path = Path(directory) / "snapshot.json"
            before = news.collect_snapshot(self.feed, self.now, path)
            self.feed.get_financial_news.side_effect = OSError("offline")
            self.feed.get_external_news.return_value = []
            after = news.collect_snapshot(self.feed, self.now.replace(hour=15), path)
            for key in ("domestic", "geopolitics"):
                self.assertEqual(after["news"][key]["items"], before["news"][key]["items"])
                self.assertEqual(after["news"][key]["generated_at"], before["news"][key]["generated_at"])
                self.assertTrue(after["news"][key]["error"])
            self.assertGreater(after["generated_at"], before["generated_at"])
            self.assertEqual(after["us_market"], before["us_market"])

    def test_dst_and_weekend_close_guard(self):
        # NY 16:15 is Shanghai 04:15 in summer and 05:15 in winter.
        for month, hour in ((7, 4), (12, 5)):
            now = datetime(2026, month, 7 if month == 7 else 8, hour, 14, tzinfo=news.SHANGHAI)
            before = news.latest_closed_weekday(now)
            after = news.latest_closed_weekday(now.replace(minute=15))
            self.assertLess(before, after)
        self.assertEqual(news.latest_closed_weekday(self.now).isoformat(), "2026-10-02")

    def test_complete_close_saved_once_and_incomplete_set_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            with patch.object(news, "get_us_quotes", return_value=quotes()) as fetch:
                first = news.collect_snapshot(self.feed, self.now, path)
                news.collect_snapshot(self.feed, self.now.replace(hour=15), path)
                self.assertEqual(fetch.call_count, 1)
            next_close = datetime(2026, 10, 6, 5, 17, tzinfo=news.SHANGHAI)
            for invalid in (quotes("2026-10-05")[:2], quotes("2026-10-06"), quotes("2026-10-05", "12:00:00"), quotes("2026-10-05", "15:00:00"),
                            [quotes("2026-10-05")[0], *quotes()[1:]]):
                with patch.object(news, "get_us_quotes", return_value=invalid):
                    after = news.collect_snapshot(self.feed, next_close, path)
                self.assertEqual(after["us_market"], first["us_market"])
                self.assertTrue(after["us_market_error"])

    def test_quote_parser_uses_correct_fields_and_rejects_invalid_prices(self):
        records = []
        for index, (symbol, _) in enumerate(news.US_INDICES):
            fields = [""] * 33
            fields[3], fields[30], fields[32] = str(100 + index), "2026-10-02 16:30:00", "-1.25"
            records.append('v_us' + symbol + '="' + "~".join(fields) + '";')
        self.feed._request.return_value = Mock(content="\n".join(records).encode("gb18030"))
        parsed = news.get_us_quotes(self.feed)
        self.assertEqual(len(parsed), 3)
        self.assertEqual(parsed[2]["name"], "纳斯达克综合")
        self.assertEqual(parsed[2]["price"], 102)
        self.assertEqual(parsed[2]["change_pct"], -1.25)
        self.feed._request.return_value.content = 'v_us.DJI="~~~nan~~~~~~~~~~~~~~~~~~~~~~~~~~~2026-10-02 16:30:00~~1";'.encode()
        self.assertEqual(news.get_us_quotes(self.feed), [])

    def test_public_snapshot_sync_and_api_read_cache_without_collecting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market_news.json"
            payload = {"schema_version": 1, "generated_at": "2026-10-04 13:00:00", "news": {}}
            def download(relative):
                if relative == "research/market_news.json":
                    return json.dumps(payload).encode()
                raise OSError("unrelated snapshot unavailable")
            with patch.object(research_core, "RESEARCH_DIR", Path(directory)), \
                    patch.object(research_core, "_download_public", side_effect=download), \
                    patch.dict("os.environ", {"SNAPSHOT_REMOTE_ENABLED": "1"}):
                research_core.sync_public_snapshots(force=True)
            self.assertEqual(json.loads(path.read_text()), payload)
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.ApiHandler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            client = build_opener(ProxyHandler({}))
            url = f"http://127.0.0.1:{httpd.server_port}/api/research/news"
            try:
                with patch.object(news, "SNAPSHOT_FILE", path), patch.object(news, "sync_public_snapshots"), \
                        patch.object(news, "collect_snapshot", side_effect=AssertionError("must not collect")):
                    for _ in range(2):
                        with client.open(url) as response:
                            self.assertEqual(json.load(response), payload)
                    path.unlink()
                    with self.assertRaises(HTTPError) as error:
                        client.open(url)
                    self.assertEqual(error.exception.code, 503)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
