"""Masker: document / search_result ブロックと、extra で渡した messages / system も送る前に伏せる.

通信はしない。名前・アドレスは架空。
"""

from __future__ import annotations

import json

from devtools_common.llm import MASK, LLMClient, Masker

SECRETS = ("hanako@example.org", "090-1234-5678", "架空商事")


def _client():
    return LLMClient("claude-haiku-4-5", masker=Masker(words=["架空商事"]), api_key="dummy")


def _assert_masked(obj):
    sent = json.dumps(obj, ensure_ascii=False)
    for s in SECRETS:
        assert s not in sent, s


def test_document_block_text_source_title_and_context_are_masked():
    doc = {
        "type": "document",
        "title": "架空商事 議事録",
        "context": "連絡先 hanako@example.org",
        "source": {"type": "text", "media_type": "text/plain", "data": "担当 090-1234-5678 架空商事"},
        "citations": {"enabled": True},
    }
    body = _client()._body([{"role": "user", "content": [doc]}], None, None, None, None, None, None, None)
    _assert_masked(body)
    sent = body["messages"][0]["content"][0]
    assert sent["source"] == {"type": "text", "media_type": "text/plain", "data": f"担当 {MASK} {MASK}"}
    assert sent["title"] == f"{MASK} 議事録"
    assert sent["citations"] == {"enabled": True}
    assert doc["source"]["data"] == "担当 090-1234-5678 架空商事"  # 呼び出し側の値は書き換えない


def test_document_block_content_source_and_search_result_are_masked():
    doc = {
        "type": "document",
        "source": {"type": "content", "content": [{"type": "text", "text": "hanako@example.org"}]},
    }
    sr = {
        "type": "search_result",
        "source": "https://example.org/a",
        "title": "架空商事",
        "content": [{"type": "text", "text": "090-1234-5678"}],
    }
    body = _client()._body([{"role": "user", "content": [doc, sr]}], None, None, None, None, None, None, None)
    _assert_masked(body)
    assert body["messages"][0]["content"][1]["source"] == "https://example.org/a"


def test_base64_document_is_left_as_is():
    doc = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "JVBERi0xLjQK"}}
    body = _client()._body([{"role": "user", "content": [doc]}], None, None, None, None, None, None, None)
    assert body["messages"][0]["content"][0] == doc


def test_messages_and_system_given_via_extra_are_masked():
    extra = {
        "messages": [{"role": "user", "content": "架空商事の hanako@example.org に連絡"}],
        "system": [{"type": "text", "text": "電話 090-1234-5678"}],
        "metadata": {"user_id": "u-1"},
    }
    body = _client()._body("無害な本文", None, None, None, None, None, None, extra)
    _assert_masked(body)
    assert body["messages"] == [{"role": "user", "content": f"{MASK}の {MASK} に連絡"}]
    assert body["metadata"] == {"user_id": "u-1"}
    assert extra["messages"][0]["content"] == "架空商事の hanako@example.org に連絡"  # 呼び出し側は変えない


def test_extra_is_passed_through_unchanged_without_masker():
    c = LLMClient("claude-haiku-4-5", api_key="dummy")
    extra = {"messages": [{"role": "user", "content": "hanako@example.org"}]}
    body = c._body("x", None, None, None, None, None, None, extra)
    assert body["messages"] == extra["messages"]
