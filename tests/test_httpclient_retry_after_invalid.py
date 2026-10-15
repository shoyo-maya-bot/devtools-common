"""HttpClient: Retry-After が負・nan・inf のときの待ち時間. 通信はしない（opener を差し替える）."""

from __future__ import annotations

import io
import unittest
import urllib.error
from email.message import Message

from devtools_common.httpclient import HttpClient, HttpError


class _Resp:
    status = 200
    headers: dict[str, str] = {}

    def read(self):
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None


class RetryAfterNegativeTest(unittest.TestCase):
    def test_negative_retry_after_falls_back_to_backoff(self):
        slept: list[float] = []
        calls = []

        def opener(req, timeout):
            calls.append(1)
            if len(calls) == 1:
                hdrs = Message()
                hdrs["Retry-After"] = "-5"
                raise urllib.error.HTTPError(req.full_url, 503, "busy", hdrs, io.BytesIO(b""))
            return _Resp()

        c = HttpClient("https://api.example.test", opener=opener, sleep=slept.append, rate_per_sec=1000)
        self.assertEqual(c.get("x").status, 200)
        # 負の Retry-After は使わず指数バックオフ（その後はレート制限の待ち）
        self.assertEqual(slept[0], c.backoff_base)
        self.assertTrue(all(s >= 0 for s in slept))

    def test_backoff_never_negative(self):
        c = HttpClient("https://api.example.test")
        self.assertEqual(c._backoff(0, "-5"), c.backoff_base)
        self.assertEqual(c._backoff(0, "nan-ish"), c.backoff_base)
        self.assertEqual(c._backoff(0, "0"), 0.0)

    def test_retry_after_nan_or_inf(self):
        c = HttpClient("https://api.example.test")
        # nan・inf は使えない値として指数バックオフにする（float() は通るが time.sleep が ValueError・OverflowError）
        self.assertEqual(c._backoff(0, "inf"), c.backoff_base)
        self.assertEqual(c._backoff(1, "nan"), c.backoff_base * 2)
        self.assertEqual(c._backoff(2, "-inf"), c.backoff_base * 4)

    def test_all_failures_raise_http_error(self):
        def opener(req, timeout):
            hdrs = Message()
            hdrs["Retry-After"] = "-5"
            raise urllib.error.HTTPError(req.full_url, 503, "busy", hdrs, io.BytesIO(b""))

        c = HttpClient(
            "https://api.example.test", opener=opener, sleep=lambda s: None, rate_per_sec=1000, max_retries=1
        )
        with self.assertRaises(HttpError):
            c.get("x")


if __name__ == "__main__":
    unittest.main()
