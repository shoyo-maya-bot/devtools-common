"""Masker: tool_use の input と tool_result の content も送る前に伏せる. 通信はしない。名前・アドレスは架空."""

from __future__ import annotations

import json

from devtools_common.llm import MASK, LLMClient, Masker

SECRETS = ("hanako@example.org", "090-1234-5678", "架空商事")


def _body(messages):
    c = LLMClient("claude-haiku-4-5", masker=Masker(words=["架空商事"]), api_key="sk-test-0000000000000000")
    return c._body(messages, None, None, None, None, None, None, None)


def _assert_masked(obj):
    sent = json.dumps(obj, ensure_ascii=False)
    for s in SECRETS:
        assert s not in sent, s


def test_masker_masks_tool_use_input_and_tool_result_string():
    msgs = [
        {"role": "user", "content": "架空商事の注文を調べて"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "調べます"},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "lookup",
                    "input": {"customer": "架空商事", "contacts": ["hanako@example.org", {"tel": "090-1234-5678"}]},
                },
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "担当 hanako@example.org"}],
        },
    ]
    body = _body(msgs)
    _assert_masked(body)
    tool_use = body["messages"][1]["content"][1]
    assert tool_use["input"] == {"customer": MASK, "contacts": [MASK, {"tel": MASK}]}  # キーはそのまま
    assert (tool_use["id"], tool_use["name"]) == ("toolu_1", "lookup")
    result = body["messages"][2]["content"][0]
    assert result == {"type": "tool_result", "tool_use_id": "toolu_1", "content": f"担当 {MASK}"}


def test_masker_masks_tool_result_block_list_and_keeps_images():
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0K"}}
    msgs = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_2",
                    "is_error": False,
                    "content": [{"type": "text", "text": "架空商事 090-1234-5678"}, image],
                }
            ],
        }
    ]
    body = _body(msgs)
    _assert_masked(body)
    block = body["messages"][0]["content"][0]
    assert block["content"] == [{"type": "text", "text": f"{MASK} {MASK}"}, image]
    assert (block["tool_use_id"], block["is_error"]) == ("toolu_2", False)
    assert msgs[0]["content"][0]["content"][0]["text"] == "架空商事 090-1234-5678"  # 呼び出し側の値は変えない
