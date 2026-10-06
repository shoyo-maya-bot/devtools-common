"""認証ヘッダーを base_url と別のホストへ送らないことの検査.

127.0.0.1 で 2 台の HTTP サーバーを立てる（1 台目 = base_url、2 台目 = 別のホスト）。外部には通信しない。
"""

from __future__ import annotations

import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from devtools_common.httpclient import CrossOriginError, HttpClient

SECRET = "Bearer test-token-not-real-0000"


class _Handler(BaseHTTPRequestHandler):
    def _handle(self):
        srv = self.server
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        with srv.lock:
            srv.seen.append({k.lower(): v for k, v in self.headers.items()})
        if srv.redirect_to:
            self.send_response(302)
            self.send_header("Location", srv.redirect_to + self.path)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    do_GET = do_POST = _handle

    def log_message(self, *a):
        pass


class Server:
    def __init__(self, redirect_to: str | None = None):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.srv.daemon_threads = True
        self.srv.seen, self.srv.lock, self.srv.redirect_to = [], threading.Lock(), redirect_to
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)

    @property
    def seen(self) -> list[dict[str, str]]:
        return self.srv.seen

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()
        return False


def _client(url: str) -> HttpClient:
    return HttpClient(url, headers={"Authorization": SECRET, "X-Api-Key": "k-not-real"}, rate_per_sec=1000)


class CrossOriginTest(unittest.TestCase):
    def test_absolute_url_to_other_host_never_receives_auth(self):
        with Server() as base, Server() as other:
            client = _client(base.url)
            with self.assertRaises(CrossOriginError):
                client.get(other.url + "/steal")
            with self.assertRaises(CrossOriginError):
                client.post(other.url + "/steal", json_body={"a": 1})
        for h in other.seen:
            self.assertNotIn("authorization", h)
            self.assertNotIn("x-api-key", h)
        self.assertEqual(base.seen, [])

    def test_other_origin_allowed_explicitly_without_default_headers(self):
        with Server() as base, Server() as other:
            res = _client(base.url).get(other.url + "/public", allow_other_origin=True, headers={"X-Trace": "1"})
        self.assertEqual(res.status, 200)
        self.assertEqual(len(other.seen), 1)
        self.assertNotIn("authorization", other.seen[0])
        self.assertNotIn("x-api-key", other.seen[0])
        self.assertEqual(other.seen[0].get("x-trace"), "1")  # 呼び出しごとのヘッダーは送る

    def test_same_origin_absolute_url_keeps_auth(self):
        # 後方互換: base_url と同じホストの絶対 URL（ページングの next リンク等）はこれまでどおり認証付き
        with Server() as base:
            client = _client(base.url + "/api/")
            client.get(base.url + "/api/items?page=2")
            client.get("items")
        self.assertEqual([h.get("authorization") for h in base.seen], [SECRET, SECRET])

    def test_default_port_is_same_origin(self):
        from devtools_common.httpclient import _origin

        self.assertEqual(_origin("https://Api.Example.com/x"), _origin("https://api.example.com:443/y"))
        self.assertNotEqual(_origin("https://api.example.com"), _origin("http://api.example.com"))
        self.assertNotEqual(_origin("https://api.example.com"), _origin("https://api.example.com:8443"))

    def test_redirect_to_other_host_drops_auth(self):
        with Server() as other, Server(redirect_to=other.url) as base:
            res = _client(base.url).get("items")
        self.assertEqual(res.status, 200)
        self.assertEqual(base.seen[0].get("authorization"), SECRET)
        self.assertEqual(len(other.seen), 1)
        self.assertNotIn("authorization", other.seen[0])
        self.assertNotIn("x-api-key", other.seen[0])


if __name__ == "__main__":
    unittest.main()
