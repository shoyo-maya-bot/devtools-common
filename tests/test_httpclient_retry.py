"""POST など冪等でないリクエストを二重送信しないことの検査.

ローカルの HTTP サーバー（127.0.0.1）か opener の差し替えだけを使い、外部には通信しない。
"""

from __future__ import annotations

import socket
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from devtools_common.httpclient import HttpClient, HttpError


class _Handler(BaseHTTPRequestHandler):
    def _handle(self):
        srv = self.server
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        with srv.lock:
            srv.hits.append(self.command)
        if srv.delay:
            time.sleep(srv.delay)
        try:
            self.send_response(srv.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
        except OSError:
            pass  # クライアントがタイムアウトで切断したとき

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _handle

    def log_message(self, *a):
        pass


class LocalServer:
    def __init__(self, status=200, delay=0.0):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.srv.daemon_threads = True
        self.srv.hits, self.srv.lock = [], threading.Lock()
        self.srv.status, self.srv.delay = status, delay
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)

    @property
    def hits(self):
        return self.srv.hits

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()
        return False


def _client(url, **kw):
    kw.setdefault("sleep", lambda s: None)
    return HttpClient(url, rate_per_sec=1000, **kw)


class FakeResp:
    def __init__(self, status=200, body=b"{}"):
        self.status, self.headers, self._body = status, {}, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener_failing(errors):
    """errors を順に送出し、尽きたら 200 を返す opener."""
    calls = []

    def opener(req, timeout):
        calls.append(req.get_method())
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        return FakeResp()

    return opener, calls


def _http_error(code, headers=None):
    return urllib.error.HTTPError("http://x", code, "err", headers or {}, None)


class PostNotDuplicatedTest(unittest.TestCase):
    """サーバーに届いた可能性がある POST は再送しない."""

    def test_post_5xx_is_sent_once(self):
        with LocalServer(status=503) as s, self.assertRaises(HttpError) as cm:
            _client(s.url).post("messages", json_body={"text": "hi"})
        self.assertEqual(cm.exception.status, 503)
        self.assertEqual(s.hits, ["POST"])

    def test_post_read_timeout_is_sent_once(self):
        with LocalServer(delay=1.0) as s, self.assertRaises(HttpError):
            _client(s.url, timeout=0.2).post("messages", json_body={"text": "hi"})
        self.assertEqual(s.hits, ["POST"])

    def test_patch_5xx_is_sent_once(self):
        with LocalServer(status=502) as s, self.assertRaises(HttpError):
            _client(s.url).request("PATCH", "issues/1", json_body={"a": 1})
        self.assertEqual(s.hits, ["PATCH"])

    def test_post_timeout_inside_urlerror_is_not_retried(self):
        # 送信中のタイムアウトは届いた可能性があるため再送しない
        opener, calls = _opener_failing([urllib.error.URLError(TimeoutError("timed out"))])
        with self.assertRaises(HttpError):
            _client("https://api.example.com", opener=opener).post("x", json_body={})
        self.assertEqual(calls, ["POST"])

    def test_post_429_without_retry_after_is_not_retried(self):
        opener, calls = _opener_failing([_http_error(429)])
        with self.assertRaises(HttpError):
            _client("https://api.example.com", opener=opener).post("x", json_body={})
        self.assertEqual(calls, ["POST"])


class PostRetriedWhenSafeTest(unittest.TestCase):
    """届いていないことが確実なとき・呼び出し側が冪等と宣言したときは再送する."""

    def test_post_connection_refused_is_retried(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()  # 何も listen していないポート
        calls = []

        def opener(req, timeout):
            calls.append(req.get_method())
            return urllib.request.urlopen(req, timeout=timeout)

        with self.assertRaises(HttpError):
            _client(f"http://127.0.0.1:{port}", opener=opener, max_retries=2).post("x", json_body={})
        self.assertEqual(calls, ["POST"] * 3)

    def test_post_dns_failure_is_retried(self):
        opener, calls = _opener_failing([urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))])
        r = _client("https://api.example.com", opener=opener).post("x", json_body={})
        self.assertEqual(r.status, 200)
        self.assertEqual(len(calls), 2)

    def test_post_429_with_retry_after_is_retried(self):
        sleeps = []
        opener, calls = _opener_failing([_http_error(429, {"Retry-After": "3"})])
        r = _client("https://api.example.com", opener=opener, sleep=sleeps.append).post("x", json_body={})
        self.assertEqual(r.status, 200)
        self.assertEqual(len(calls), 2)
        self.assertIn(3.0, sleeps)

    def test_post_idempotent_opt_in_is_retried(self):
        with LocalServer(status=503) as s, self.assertRaises(HttpError):
            _client(s.url, max_retries=2).post("x", json_body={}, idempotent=True)
        self.assertEqual(s.hits, ["POST"] * 3)

    def test_post_with_idempotency_key_is_retried(self):
        opener, calls = _opener_failing([_http_error(503), TimeoutError("timed out")])
        r = _client("https://api.example.com", opener=opener).post(
            "x", json_body={}, headers={"Idempotency-Key": "abc"}
        )
        self.assertEqual(r.status, 200)
        self.assertEqual(len(calls), 3)

    def test_idempotent_false_disables_retry_for_get(self):
        with LocalServer(status=503) as s, self.assertRaises(HttpError):
            _client(s.url).get("x", idempotent=False)
        self.assertEqual(s.hits, ["GET"])


class IdempotentMethodsStillRetriedTest(unittest.TestCase):
    def test_get_put_delete_retry_on_5xx(self):
        for method in ("GET", "PUT", "DELETE"):
            with self.subTest(method=method), LocalServer(status=500) as s:
                with self.assertRaises(HttpError):
                    _client(s.url, max_retries=2).request(method, "x")
                self.assertEqual(s.hits, [method] * 3)

    def test_get_read_timeout_is_retried(self):
        opener, calls = _opener_failing([TimeoutError("timed out")])
        r = _client("https://api.example.com", opener=opener).get("x")
        self.assertEqual(r.status, 200)
        self.assertEqual(calls, ["GET", "GET"])


if __name__ == "__main__":
    unittest.main()
