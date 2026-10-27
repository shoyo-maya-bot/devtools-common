"""HttpClient: 再送しても直らない失敗（証明書の検証エラー・URL の誤り）は再試行しない.
送信中の切断（BrokenPipeError）や壊れた応答は生の例外ではなく HttpError にする. 通信はしない（opener の差し替え）."""

from __future__ import annotations

import http.client
import ssl
import urllib.error

import pytest

from devtools_common.httpclient import HttpClient, HttpError, is_permanent_network_error


def _client(exc: BaseException) -> tuple[HttpClient, list[int]]:
    calls: list[int] = []

    def opener(req, timeout):
        calls.append(1)
        raise exc

    return HttpClient("https://api.example.com/", opener=opener, sleep=lambda s: None, rate_per_sec=1000), calls


@pytest.mark.parametrize(
    "exc",
    [
        urllib.error.URLError(ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] verify failed")),
        ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] verify failed"),
        urllib.error.URLError("unknown url type: htps"),
        http.client.InvalidURL("bad url"),
    ],
    ids=["urlerror-cert", "raw-cert", "url-type", "invalid-url"],
)
def test_permanent_errors_are_not_retried(exc):
    client, calls = _client(exc)
    with pytest.raises(HttpError, match="permanent"):
        client.get("items")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "exc",
    [
        BrokenPipeError(32, "Broken pipe"),
        ConnectionAbortedError(103, "aborted"),
        http.client.BadStatusLine("garbage"),
        urllib.error.URLError(ssl.SSLEOFError(8, "EOF occurred in violation of protocol")),
    ],
    ids=["broken-pipe", "aborted", "bad-status", "ssl-eof"],
)
def test_transient_errors_are_retried_and_wrapped(exc):
    client, calls = _client(exc)
    with pytest.raises(HttpError, match="network error"):
        client.get("items")
    assert len(calls) == client.max_retries + 1


def test_broken_pipe_on_post_is_not_retried_but_wrapped():
    client, calls = _client(BrokenPipeError(32, "Broken pipe"))
    with pytest.raises(HttpError):
        client.post("items", json_body={"a": 1})
    assert len(calls) == 1  # サーバーに届いた可能性があるので二重送信しない


def test_llm_uses_the_same_permanent_check():
    from devtools_common import llm

    e = urllib.error.URLError(ssl.SSLCertVerificationError(1, "verify failed"))
    assert is_permanent_network_error(e) is llm._is_permanent_connection_error(e) is True
