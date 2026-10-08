"""log.redact: Anthropic の API キーと Authorization: Basic を伏せる. キーは架空."""

from __future__ import annotations

import logging

import pytest

from devtools_common import log


@pytest.mark.parametrize(
    "text, secret",
    [
        ("key=sk-ant-api03-AbCdEfGh0123456789_xyz-QQ", "sk-ant-api03-AbCdEfGh0123456789"),
        ("using sk-ant-admin01-ZZZZZZZZZZZZZZZZZZZZ now", "ZZZZZZZZZZZZZZZZZZZZ"),
        ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ("proxy-authorization: basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
        ('{"Authorization": "Basic dXNlcjpwYXNz"}', "dXNlcjpwYXNz"),
    ],
)
def test_redact_anthropic_key_and_basic_auth(text, secret):
    out = log.redact(text)
    assert secret not in out
    assert "***" in out


def test_redact_keeps_ordinary_words():
    assert log.redact("basic な使い方と sk-ant の説明") == "basic な使い方と sk-ant の説明"


def test_redact_filter_on_log_record(caplog):
    lg = logging.getLogger("devtools_common.test_log_redact_patterns")
    flt = log.RedactFilter()
    lg.addFilter(flt)
    try:
        with caplog.at_level(logging.WARNING, logger=lg.name):
            lg.warning("header %s", "Authorization: Basic dXNlcjpwYXNz")
    finally:
        lg.removeFilter(flt)
    assert "dXNlcjpwYXNz" not in caplog.text
