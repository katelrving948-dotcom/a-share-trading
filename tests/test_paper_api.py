import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

import server
from paper_trading import new_state


class PaperApiTest(unittest.TestCase):
    def setUp(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.ApiHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.client = build_opener(ProxyHandler({}))

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()

    @patch("server.sync_public_snapshots")
    @patch("server.load_paper_state", side_effect=new_state)
    def test_read_only_public_accounts_and_removed_quant(self, *_):
        with self.client.open(self.base + "/api/paper") as response:
            data = json.load(response)
        self.assertEqual(data["accounts"]["balanced"]["initial"], 500000)
        self.assertNotIn("account", data)
        with self.assertRaises(HTTPError) as error:
            self.client.open(self.base + "/api/technical")
        self.assertEqual(error.exception.code, 404)

    @patch.dict("server.os.environ", {"CRON_SECRET": "test-secret"})
    @patch("server._dispatch_paper_workflow")
    def test_authorized_dispatch_does_not_claim_completed(self, dispatch):
        request = Request(self.base + "/api/paper/run", data=b"{}")
        with self.assertRaises(HTTPError) as error:
            self.client.open(request)
        self.assertEqual(error.exception.code, 401)
        dispatch.assert_not_called()
        request.add_header("Authorization", "Bearer test-secret")
        with self.client.open(request) as response:
            self.assertEqual(response.status, 202)
            self.assertEqual(json.load(response)["state"], "dispatched")
        dispatch.assert_called_once()
