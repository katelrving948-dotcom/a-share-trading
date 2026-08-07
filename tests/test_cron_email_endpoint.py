import json
import threading
import time
import unittest
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

    @patch.dict("os.environ", {"CRON_SECRET": "test-secret"}, clear=False)
    def test_rejects_invalid_secret(self):
        with self.assertRaises(HTTPError) as context:
            self._post("Bearer wrong-secret")
        self.assertEqual(context.exception.code, 401)

    @patch("server.send_daily_email_digest")
    @patch.dict("os.environ", {"CRON_SECRET": "test-secret"}, clear=False)
    def test_accepts_valid_secret_and_starts_email(self, send_digest):
        with self._post("Bearer test-secret") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 202)
        self.assertTrue(payload["accepted"])
        self.assertTrue(payload["started"])
        for _ in range(20):
            if send_digest.called:
                break
            time.sleep(0.05)
        send_digest.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
