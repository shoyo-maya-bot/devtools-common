"""LLMClient の接続エラーの扱い（直らない失敗は再試行しない・http.client の例外も LLMError に包む）.

通信はしない（opener を差し替えて例外を投げる）。キーは架空。
"""

from __future__ import annotations

import http.client
import ssl
import urllib.error

import pytest

from devtools_common.llm import APIConnectionError, LLMClient, LLMError


def _client(errors, sleeps, **kw):
    calls = []

    def opener(req, timeout):
        calls.append(req.full_url)
        raise errors[min(len(calls), len(errors)) - 1]

    c = LLMClient(
        "claude-haiku-4-5",
        base_url="https://llm.example.test",
        api_key="sk-test-0000000000000000",
        opener=opener,
        sleep=sleeps.append,
        **kw,
    )
    return c, calls


def _cert_error():
    e = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    e.verify_code, e.verify_message = 20, "unable to get local issuer certificate"
    return e


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(urllib.error.URLError(_cert_error()), id="urlerror-cert"),
        pytest.param(_cert_error(), id="cert"),
        pytest.param(urllib.error.URLError(ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER] wrong version")), id="tls"),
        pytest.param(urllib.error.URLError("unknown url type: htps"), id="bad-url"),
        pytest.param(http.client.InvalidURL("nonnumeric port"), id="invalid-url"),
    ],
)
def test_permanent_errors_are_not_retried(error):
    sleeps: list[float] = []
    c, calls = _client([error], sleeps, max_retries=3)
    with pytest.raises(APIConnectionError, match="permanent"):
        c.complete("hi")
    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(http.client.BadStatusLine("garbage"), id="bad-status-line"),
        pytest.param(http.client.RemoteDisconnected("closed"), id="remote-disconnected"),
        pytest.param(http.client.LineTooLong("header line"), id="line-too-long"),
        pytest.param(http.client.HTTPException("other"), id="http-exception"),
    ],
)
def test_http_client_exceptions_are_wrapped_in_llm_error(error):
    sleeps: list[float] = []
    c, calls = _client([error], sleeps, max_retries=1)
    with pytest.raises(LLMError) as ei:
        c.complete("hi")
    assert isinstance(ei.value, APIConnectionError)
    assert isinstance(ei.value.__cause__, http.client.HTTPException)


def test_bad_status_line_is_retried_as_transient():
    sleeps: list[float] = []
    c, calls = _client([http.client.BadStatusLine("garbage")], sleeps, max_retries=2)
    with pytest.raises(APIConnectionError):
        c.complete("hi")
    assert len(calls) == 3 and len(sleeps) == 2


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(urllib.error.URLError(ConnectionRefusedError(111, "refused")), id="refused"),
        pytest.param(urllib.error.URLError(TimeoutError("timed out")), id="timeout"),
        pytest.param(urllib.error.URLError(ssl.SSLEOFError(8, "EOF occurred in violation of protocol")), id="ssl-eof"),
        pytest.param(ConnectionResetError(104, "reset"), id="reset"),
    ],
)
def test_transient_errors_are_still_retried(error):
    sleeps: list[float] = []
    c, calls = _client([error], sleeps, max_retries=2)
    with pytest.raises(APIConnectionError) as ei:
        c.complete("hi")
    assert "permanent" not in str(ei.value)
    assert len(calls) == 3 and len(sleeps) == 2
