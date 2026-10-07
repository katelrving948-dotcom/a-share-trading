import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler

import research_core
import server


class WebsiteSnapshotTest(unittest.TestCase):
    def test_snapshot_survives_reload_without_private_data_or_market_fetch(self):
        payload = {
            "generated_at": "2026-09-30 12:05:00", "observations": [], "rotation_boards": [], "rules": {},
            "account": {"equity": 50000, "holdings": [{"code": "000933", "quantity": 400}]},
            "holding_actions": [{"code": "000933", "sell_quantity": 400}],
            "weekly_plan": {"account": {"equity": 50000}, "holding_actions": [{"quantity": 400}]},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "website_snapshot.json"
            research_core.save_website_snapshot(payload, path)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("equity", saved["account"])
            self.assertNotIn("holdings", saved["account"])
            self.assertNotIn("holding_actions", saved)
            self.assertEqual(saved["weekly_plan"]["holding_actions"], [])
            with patch.object(research_core, "WEBSITE_SNAPSHOT_FILE", path), \
                    patch.object(research_core, "sync_public_snapshots"), \
                    patch.object(research_core, "build_push_payload", side_effect=AssertionError("must not collect")):
                for _ in range(2):
                    loaded = research_core.load_website_snapshot()
                    self.assertEqual(loaded, saved)
                    self.assertEqual(loaded["account"]["holdings_count"], 1)
            self.assertEqual(payload["account"]["equity"], 50000)

    def test_invalid_save_and_failed_or_incomplete_remote_sync_preserve_last_snapshot(self):
        payload = {"generated_at": "2026-09-30 12:05:00", "observations": [], "rotation_boards": [], "rules": {}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "website_snapshot.json"
            research_core.save_website_snapshot(payload, path)
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                research_core.save_website_snapshot({"generated_at": "2026-10-07 12:00:00"}, path)
            for remote in [OSError("offline"), b'{"generated_at":"2026-10-07 12:00:00"}']:
                def download(relative):
                    if relative == "research/website_snapshot.json" and isinstance(remote, bytes):
                        return remote
                    raise OSError("offline")
                with patch.dict("os.environ", {"SNAPSHOT_REMOTE_ENABLED": "1"}), \
                        patch.object(research_core, "WEBSITE_SNAPSHOT_FILE", path), \
                        patch.object(research_core, "_download_public", side_effect=download):
                    research_core.sync_public_snapshots(force=True)
                self.assertEqual(path.read_bytes(), before)

    def test_background_sync_does_not_wait_for_remote_download(self):
        entered, release = threading.Event(), threading.Event()
        def download(_):
            entered.set()
            release.wait(5)
            raise OSError("offline")
        with patch.dict("os.environ", {"SNAPSHOT_REMOTE_ENABLED": "1"}), \
                patch.object(research_core, "_snapshot_sync_checked_at", 0), \
                patch.object(research_core, "_download_public", side_effect=download):
            try:
                research_core.sync_public_snapshots(background=True)
                self.assertTrue(entered.wait(2))
                self.assertFalse(release.is_set())
            finally:
                release.set()
                with research_core._snapshot_background_lock:
                    pass

    def test_preview_reads_saved_snapshot_and_missing_snapshot_returns_503(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.ApiHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        client = build_opener(ProxyHandler({}))
        url = f"http://127.0.0.1:{httpd.server_port}/api/push/preview"
        try:
            with patch.object(server, "load_website_snapshot", return_value={"generated_at": "2026-09-30 12:05:00"}):
                with client.open(url) as response:
                    self.assertEqual(json.load(response)["generated_at"], "2026-09-30 12:05:00")
            with patch.object(server, "load_website_snapshot", return_value={}):
                with self.assertRaises(HTTPError) as error:
                    client.open(url)
                self.assertEqual(error.exception.code, 503)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
