"""再試行の待ち時間（retry_delay）・受信中の通信エラー・JSON でない成功応答の回帰テスト.

opener を差し替えるだけで、外部には通信しない。
"""

from __future__ import annotations

import email.utils
import http.client
import math
import time

import pytest

from devtools_common.httpclient import HttpClient, HttpError, parse_retry_after, retry_delay
from devtools_common.llm import APIConnectionError, LLMClient, LLMError

API_KEY = "sk-test-not-a-real-key-0000000000"

# ---------------------------------------------------------------- 待ち時間


UNUSABLE = [None, "", "  ", "abc", "nan", "NaN", "inf", "-inf", "Infinity", "-5", "-0.1", "1e999"]


@pytest.mark.parametrize("value", UNUSABLE)
def test_unusable_retry_after_is_none(value):
    assert parse_retry_after(value) is None


@pytest.mark.parametrize(("value", "want"), [("0", 0.0), ("2", 2.0), (" 1.5 ", 1.5), ("120", 120.0)])
def test_retry_after_seconds(value, want):
    assert parse_retry_after(value) == want


def test_retry_after_http_date():
    now = 1_800_000_000.0
    future = email.utils.formatdate(now + 30, usegmt=True)
    past = email.utils.formatdate(now - 30, usegmt=True)
    assert parse_retry_after(future, now=lambda: now) == pytest.approx(30.0)
    assert parse_retry_after(past, now=lambda: now) == 0.0


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "-5", "abc", "1e999", None])
def test_invalid_retry_after_falls_back_to_backoff(value):
    assert retry_delay(0, value, base=0.5, cap=30.0) == 0.5
    assert retry_delay(3, value, base=0.5, cap=30.0) == 4.0
    assert retry_delay(10, value, base=0.5, cap=30.0) == 30.0  # 上限で丸める


def test_retry_after_is_capped_and_finite():
    assert retry_delay(0, "9999", base=1.0, cap=5.0) == 5.0
    assert retry_delay(0, "2", base=1.0, cap=5.0) == 2.0
    # attempt が大きくても OverflowError にならない
    d = retry_delay(10_000, None, base=1.0, cap=60.0)
    assert d == 60.0 and math.isfinite(d)


def test_jitter_ranges():
    hi = lambda a, b: b  # noqa: E731 — ゆらぎの上限を取る
    lo = lambda a, b: a  # noqa: E731
    assert retry_delay(0, "2", base=0.5, cap=60, jitter=True, uniform=hi) == 2.25
    assert retry_delay(0, "nan", base=1.0, cap=60, jitter=True, uniform=lo) == 0.5
    assert retry_delay(0, "nan", base=1.0, cap=60, jitter=True, uniform=hi) == 1.0


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "-5", "garbage"])
def test_llm_delay_never_raises_and_is_bounded(value):
    c = LLMClient("claude-haiku-4-5", api_key=API_KEY, backoff_base=1.0, backoff_max=10.0)
    for attempt in range(6):
        d = c._delay(attempt, value)
        assert math.isfinite(d) and 0 <= d <= 10.0 + 0.5
        time.sleep(0)  # time.sleep が受け付ける値（ValueError・OverflowError にならない）


# ---------------------------------------------------------------- 受信中の通信エラー


class _Resp:
    def __init__(self, body=b"{}", status=200, headers=None, exc=None):
        self.status, self.headers, self._body, self._exc = status, headers or {}, body, exc

    def read(self):
        if self._exc:
            raise self._exc
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _scripted(*items):
    """items を順に返す opener. 例外なら送出し、_Resp ならそれを返す. 尽きたら 200."""
    calls = []

    def opener(req, timeout):
        calls.append(req.get_method())
        item = items[len(calls) - 1] if len(calls) <= len(items) else _Resp()
        if isinstance(item, BaseException):
            raise item
        return item

    return opener, calls


TRANSIENT = [
    http.client.RemoteDisconnected("Remote end closed connection without response"),
    ConnectionResetError(104, "Connection reset by peer"),
]


@pytest.mark.parametrize("exc", TRANSIENT, ids=type)
def test_httpclient_retries_transient_errors_on_get(exc):
    opener, calls = _scripted(exc)
    r = HttpClient("https://api.example.test", opener=opener, sleep=lambda s: None, rate_per_sec=1000).get("x")
    assert r.status == 200 and calls == ["GET", "GET"]


def test_httpclient_retries_incomplete_read_on_get():
    opener, calls = _scripted(_Resp(exc=http.client.IncompleteRead(b"{", 10)))
    r = HttpClient("https://api.example.test", opener=opener, sleep=lambda s: None, rate_per_sec=1000).get("x")
    assert r.status == 200 and calls == ["GET", "GET"]


@pytest.mark.parametrize("exc", [*TRANSIENT, http.client.IncompleteRead(b"", 5)], ids=lambda e: type(e).__name__)
def test_httpclient_transient_errors_become_http_error(exc):
    opener, calls = _scripted(*[exc] * 5)
    c = HttpClient("https://api.example.test", opener=opener, sleep=lambda s: None, rate_per_sec=1000, max_retries=2)
    with pytest.raises(HttpError) as ei:
        c.get("x")
    assert ei.value.status is None and "network error" in str(ei.value)
    assert calls == ["GET"] * 3


def test_httpclient_post_not_resent_after_disconnect():
    # 応答の受信中に切れた POST はサーバーに届いた可能性があるので再送しない
    opener, calls = _scripted(http.client.RemoteDisconnected("closed"))
    c = HttpClient("https://api.example.test", opener=opener, sleep=lambda s: None, rate_per_sec=1000)
    with pytest.raises(HttpError):
        c.post("x", json_body={})
    assert calls == ["POST"]


def test_llm_retries_incomplete_read_then_raises_connection_error():
    opener, calls = _scripted(*[_Resp(exc=http.client.IncompleteRead(b"{", 10))] * 5)
    c = LLMClient("claude-haiku-4-5", api_key=API_KEY, opener=opener, sleep=lambda s: None, max_retries=2)
    with pytest.raises(APIConnectionError):
        c.complete("hi")
    assert len(calls) == 3


# ---------------------------------------------------------------- JSON でない成功応答


@pytest.mark.parametrize(
    "body",
    [b"<html>proxy login</html>", b"", b"\xff\xfe\x00", b"[1, 2]", b'"text"'],
    ids=["html", "empty", "not-utf8", "array", "string"],
)
def test_llm_non_json_200_is_llm_error(body):
    opener, _ = _scripted(_Resp(body=body, headers={"content-type": "text/html", "request-id": "req_x"}))
    c = LLMClient("claude-haiku-4-5", api_key=API_KEY, opener=opener, sleep=lambda s: None)
    with pytest.raises(LLMError) as ei:
        c.complete("hi")
    msg = str(ei.value)
    assert "HTTP 200" in msg and "req_x" in msg
    assert "proxy login" not in msg  # 本文は例外に出さない
