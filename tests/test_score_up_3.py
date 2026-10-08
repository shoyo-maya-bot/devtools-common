"""第 3 回の採点で再現した不具合の回帰テスト（伏せ字・.env・ログの伏せ字・JSON レポートのスキーマ）.

通信はしない（127.0.0.1 も使わず、送る直前の本文 `_body` を確かめる）。名前・アドレス・キーはすべて架空。
"""

from __future__ import annotations

import json
import logging
import math
import os

import jsonschema
import pytest

from devtools_common import config, log, report
from devtools_common.llm import MASK, LLMClient, Masker
from devtools_common.report import Finding, Severity

SECRETS = ("hanako@example.org", "090-1234-5678", "架空商事")


def _body(messages):
    c = LLMClient("claude-haiku-4-5", masker=Masker(words=["架空商事"]), api_key="sk-test-0000000000000000")
    return c._body(messages, None, None, None, None, None, None, None)


def _assert_masked(obj):
    sent = json.dumps(obj, ensure_ascii=False)
    for s in SECRETS:
        assert s not in sent, s


# ---------------------------------------------------------------- 1. Masker: tool_use・tool_result


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


# ---------------------------------------------------------------- 2. load_dotenv: BOM・行末コメント


@pytest.fixture
def clean_env():
    names = ["DT3_A", "DT3_B", "DT3_C", "DT3_D", "DT3_E", "DT3_F"]
    saved = {n: os.environ.pop(n, None) for n in names}
    yield
    for n, v in saved.items():
        os.environ.pop(n, None)
        if v is not None:
            os.environ[n] = v


def test_dotenv_with_bom_keeps_first_key(tmp_path, clean_env):
    p = tmp_path / ".env"
    p.write_bytes("DT3_A=1\nDT3_B=2\n".encode("utf-8-sig"))
    loaded = config.load_dotenv(p)
    assert loaded == {"DT3_A": "1", "DT3_B": "2"}
    assert os.environ["DT3_A"] == "1"


def test_dotenv_strips_inline_comment_outside_quotes(tmp_path, clean_env):
    p = tmp_path / ".env"
    p.write_text(
        "DT3_A=abc # コメント\n"
        'DT3_B="x # y" # 引用符の外のコメント\n'
        "DT3_C='p#q'\n"
        "DT3_D=a#b\n"
        "DT3_E=   # 値なし\n"
        'DT3_F="unterminated\n',
        encoding="utf-8",
    )
    assert config.load_dotenv(p) == {
        "DT3_A": "abc",
        "DT3_B": "x # y",
        "DT3_C": "p#q",
        "DT3_D": "a#b",
        "DT3_E": "",
        "DT3_F": '"unterminated',
    }


# ---------------------------------------------------------------- 3. 伏せ字: sk-ant-…・Authorization: Basic


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
    lg = logging.getLogger("devtools_common.test_score_up_3")
    flt = log.RedactFilter()
    lg.addFilter(flt)
    try:
        with caplog.at_level(logging.WARNING, logger=lg.name):
            lg.warning("header %s", "Authorization: Basic dXNlcjpwYXNz")
    finally:
        lg.removeFilter(flt)
    assert "dXNlcjpwYXNz" not in caplog.text


# ---------------------------------------------------------------- 4. to_json_obj がスキーマに合う


def test_to_json_obj_drops_meta_values_the_schema_rejects(caplog):
    findings = [Finding("R-1", Severity.ERROR, "msg", location="row 1")]
    meta = {"input": "a.csv", "records": 3, "ratio": 0.5, "ok": True, "none": None, "list": [1], "map": {"a": 1}}
    meta["nan"] = math.nan
    with caplog.at_level(logging.WARNING, logger="devtools_common.report"):
        obj = report.to_json_obj(findings, title="T", meta=meta, tool="x", tool_version="1")
    jsonschema.validate(obj, report.report_schema())
    assert obj["meta"] == {"input": "a.csv", "records": 3, "ratio": 0.5, "ok": True}
    assert "none" in caplog.text and "nan" in caplog.text
    # render(json) も同じ（NaN をそのまま JSON に書かない）
    text = report.render(findings, "json", title="T", meta=meta)
    jsonschema.validate(json.loads(text), report.report_schema())
    assert "NaN" not in text


def test_to_json_obj_coerces_non_string_fields():
    f = Finding("R-1", Severity.WARNING, "msg", location=12, expected=3.5)  # type: ignore[arg-type]
    obj = report.to_json_obj([f], title=1, meta={1: "a"}, tool_version=2)  # type: ignore[arg-type]
    jsonschema.validate(obj, report.report_schema())
    assert obj["findings"][0]["location"] == "12"
    assert obj["meta"] == {"1": "a"}
