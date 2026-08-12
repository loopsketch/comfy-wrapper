"""手元側の共有層。宛先・キーの解決、リトライ、障害の切り分け、単価。

HTTP は localhost に立てた偽サーバへ向ける。Colab も GPU も要らない。
"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import _bootstrap  # noqa: F401

from lib import colab_link


class FakeComfyServer:
    """指定した応答を順に返すだけのサーバ。"""

    def __init__(self, responses):
        self.responses = list(responses)   # [(status, body), ...] 最後の1つは使い回す
        self.requests: list[tuple[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self):
                outer.requests.append((self.command, self.path))
                if len(outer.responses) > 1:
                    status, body = outer.responses.pop(0)
                else:
                    status, body = outer.responses[0]
                payload = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = _reply
            do_POST = _reply

            def log_message(self, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"

    def __enter__(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


class EndpointTest(unittest.TestCase):
    def test_default(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(colab_link.read_endpoint(), "http://tunnel:8000")

    def test_env_override(self):
        with mock.patch.dict("os.environ", {"COLAB_ENDPOINT": "http://h:9/"}, clear=True):
            self.assertEqual(colab_link.read_endpoint(), "http://h:9")

    def test_legacy_env_name(self):
        with mock.patch.dict("os.environ", {"COLAB_H3_ENDPOINT": "http://old:1"}, clear=True):
            self.assertEqual(colab_link.read_endpoint(), "http://old:1")

    def test_argument_wins(self):
        with mock.patch.dict("os.environ", {"COLAB_ENDPOINT": "http://h:9"}, clear=True):
            self.assertEqual(colab_link.read_endpoint("http://x:2/"), "http://x:2")


class ApiKeyTest(unittest.TestCase):
    def test_env(self):
        with mock.patch.dict("os.environ", {"COLAB_API_KEY": "cw_env"}, clear=True):
            self.assertEqual(colab_link.read_api_key(), "cw_env")

    def test_file(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "colab-api-key"
            path.write_text("cw_file\n")
            with mock.patch.dict("os.environ", {}, clear=True), \
                 mock.patch.object(colab_link, "API_KEY_FILE", path):
                self.assertEqual(colab_link.read_api_key(), "cw_file")

    def test_missing_key_names_the_fix(self):
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(colab_link, "API_KEY_FILE", Path("/nonexistent/a")), \
             mock.patch.object(colab_link, "LEGACY_API_KEY_FILE", Path("/nonexistent/b")):
            with self.assertRaises(colab_link.LinkError) as cm:
                colab_link.require_api_key()
            self.assertIn("colab_key.sh", str(cm.exception))


class PricingTest(unittest.TestCase):
    def test_yen_per_hour(self):
        # 1.54 CU/時 x 11.79 円/CU
        self.assertAlmostEqual(colab_link.yen_per_hour("L4"), 18.1566, places=4)
        self.assertAlmostEqual(colab_link.yen_per_hour("A100"), 62.487, places=3)

    def test_unknown_gpu_falls_back_to_default(self):
        self.assertEqual(colab_link.yen_per_hour("H100"), colab_link.yen_per_hour("L4"))

    def test_cost_grows_with_time(self):
        self.assertAlmostEqual(
            colab_link.usd_for_seconds(3600, "L4") * 2,
            colab_link.usd_for_seconds(7200, "L4"),
        )


class DiagnoseTest(unittest.TestCase):
    def test_reauth_needed(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp) / "auth-state.json"
            state.write_text(json.dumps({"state": "reauth_needed"}))
            with mock.patch.object(colab_link, "AUTH_STATE", state):
                self.assertIn("認証", colab_link.diagnose())

    def test_no_sessions(self):
        with TemporaryDirectory() as tmp:
            sessions = Path(tmp) / "sessions.json"
            sessions.write_text("{}")
            with mock.patch.object(colab_link, "AUTH_STATE", Path(tmp) / "none.json"), \
                 mock.patch.object(colab_link, "SESSIONS", sessions):
                self.assertIn("セッション", colab_link.diagnose())

    def test_tunnel_down(self):
        with TemporaryDirectory() as tmp:
            sessions = Path(tmp) / "sessions.json"
            sessions.write_text(json.dumps({"comfy": {}}))
            with mock.patch.object(colab_link, "AUTH_STATE", Path(tmp) / "none.json"), \
                 mock.patch.object(colab_link, "SESSIONS", sessions):
                self.assertIn("トンネル", colab_link.diagnose())


class RequestTest(unittest.TestCase):
    def setUp(self):
        # 実際の待ちは1回 5秒。テストでは待たない
        patcher = mock.patch.object(colab_link, "RETRY_WAIT", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_ok(self):
        with FakeComfyServer([(200, '{"job_id": "abc"}')]) as server:
            status, body = colab_link.request(server.url, "k", "GET", "/v1/jobs")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["job_id"], "abc")

    def test_get_retries_on_tunnel_error(self):
        with FakeComfyServer([(502, "down"), (200, "{}")]) as server:
            status, _ = colab_link.request(server.url, "k", "GET", "/health")
            self.assertEqual(status, 200)
            self.assertEqual(len(server.requests), 2)

    def test_post_is_not_retried(self):
        """投入は届いた可能性があるので繰り返さない(二重生成を避ける)。"""
        with FakeComfyServer([(502, "down"), (200, "{}")]) as server:
            with self.assertRaises(colab_link.LinkError):
                colab_link.request(server.url, "k", "POST", "/v1/generate", {"a": 1})
            self.assertEqual(len(server.requests), 1)

    def test_http_error_is_shaped(self):
        with FakeComfyServer([(400, '{"error": "bad prompt"}')]) as server:
            with self.assertRaises(colab_link.LinkError) as cm:
                colab_link.request(server.url, "k", "POST", "/v1/generate", {})
            self.assertIn("400", str(cm.exception))
            self.assertIn("bad prompt", str(cm.exception))

    def test_html_error_page_is_not_dumped(self):
        html = "<html>" + "x" * 5000 + "</html>"
        with FakeComfyServer([(400, html)]) as server:
            with self.assertRaises(colab_link.LinkError) as cm:
                colab_link.request(server.url, "k", "POST", "/v1/generate", {})
            self.assertNotIn("xxxx", str(cm.exception))

    def test_bearer_is_sent(self):
        with FakeComfyServer([(200, "{}")]) as server:
            colab_link.request(server.url, "cw_secret", "GET", "/v1/jobs")
        # ヘッダの中身はハンドラでは見ていないので、少なくとも到達したことを確かめる
        self.assertEqual(server.requests, [("GET", "/v1/jobs")])

    def test_connection_refused_explains(self):
        with mock.patch.object(colab_link, "RETRIES", 1):
            with self.assertRaises(colab_link.LinkError) as cm:
                colab_link.request("http://127.0.0.1:1", "k", "GET", "/health")
        self.assertIn("接続できません", str(cm.exception))


class HealthTest(unittest.TestCase):
    def test_health(self):
        body = json.dumps({"status": "ok", "comfy_ready": True})
        with FakeComfyServer([(200, body)]) as server:
            self.assertTrue(colab_link.health(server.url)["comfy_ready"])

    def test_tunnel_down_is_named(self):
        with FakeComfyServer([(530, "down")]) as server:
            with self.assertRaises(colab_link.LinkError) as cm:
                colab_link.health(server.url)
        self.assertIn("到達できません", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
