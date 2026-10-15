"""complete_json が自分で決める引数（tools・tool_choice）を渡されたときの扱い."""

from __future__ import annotations

import pytest

from devtools_common.llm import FakeLLM

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def _fake():
    return FakeLLM(
        [
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-4-5",
                "content": [{"type": "tool_use", "id": "toolu_1", "name": "record", "input": {"ok": True}}],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        ]
    )


@pytest.mark.parametrize(
    "kw, names",
    [
        ({"tools": [{"name": "x", "input_schema": {"type": "object"}}]}, "tools"),
        ({"tool_choice": {"type": "auto"}}, "tool_choice"),
        ({"tools": [], "tool_choice": {"type": "any"}}, "tool_choice, tools"),
    ],
)
def test_reserved_keywords_raise_value_error(kw, names):
    with pytest.raises(ValueError, match=f"does not accept {names}.*complete\\(\\)"):
        _fake().complete_json("hi", schema=SCHEMA, **kw)


def test_other_keywords_still_pass_through():
    assert _fake().complete_json("hi", schema=SCHEMA, system="s", max_tokens=10) == {"ok": True}
