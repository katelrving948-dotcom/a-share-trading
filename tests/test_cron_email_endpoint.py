import json
import threading
import time
import unittest
from datetime import datetime
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import server


class CronEmailEndpointTest(unittest.TestCase):
    def setUp(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.ApiHandler)
        self.base_url = f"http://127.0.0.1:{self.httpd.server_port}"
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.server_thread.join(timeout=2)

    def _post(self, authorization=None):
        headers = {}
        if authorization:
            headers["Authorization"] = authorization
        request = Request(
            f"{self.base_url}/api/cron/daily-email",
            data=b"{}",
            headers=headers,
            method="POST",
        )
        return urlopen(request, timeout=3)

    def test_wake_endpoint_returns_no_content(self):
        with urlopen(f"{self.base_url}/api/cron/wake", timeout=3) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.read(), b"")

    @patch.dict("os.environ", {"CRON_SECRET": "test-secret"}, clear=False)
    def test_rejects_invalid_secret(self):
        with self.assertRaises(HTTPError) as context:
            self._post("Bearer wrong-secret")
        self.assertEqual(context.exception.code, 401)

    @patch("server._dispatch_daily_email_workflow")
    @patch.dict("os.environ", {"CRON_SECRET": "test-secret", "CRON_WINDOW_BYPASS": "1"}, clear=False)
    def test_accepts_valid_secret_and_dispatches_workflow(self, dispatch_workflow):
        with self._post("Bearer test-secret") as response:
            body = response.read().decode("utf-8")
            payload = json.loads(body) if body else {}

        self.assertEqual(response.status, 204)
        self.assertEqual(payload, {})
        for _ in range(20):
            if dispatch_workflow.called:
                break
            time.sleep(0.05)
        dispatch_workflow.assert_called_once_with()

    def test_scheduled_push_only_accepts_weekday_noon_window(self):
        self.assertTrue(server._scheduled_push_allowed(datetime(2026, 8, 17, 12, 0)))
        self.assertFalse(server._scheduled_push_allowed(datetime(2026, 8, 17, 10, 0)))
        self.assertFalse(server._scheduled_push_allowed(datetime(2026, 8, 16, 12, 0)))


if __name__ == "__main__":
    unittest.main()
