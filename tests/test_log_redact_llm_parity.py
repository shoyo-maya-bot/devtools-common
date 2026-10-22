"""log.redact が llm.SECRET_PATTERNS と同じ種類のトークンと JSON 形のキーを伏せること. キーはすべて架空."""

from __future__ import annotations

import pytest

from devtools_common import llm, log
from devtools_common.cli import run_main

SAMPLES = [
    "github_pat_" + "A1b2C3d4E5f6G7h8I9j0K1_" + "x" * 30,
    "sk-proj-" + "Ab12" * 8,
    "sk-" + "Zy98" * 6,
    "ASIA" + "ABCDEFGHIJKLMNOP",
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "ghp_" + "a" * 36,
    "xoxb-" + "1234567890-abcdef",
    "xoxa-" + "1234567890-abcdef",
    "sk-ant-api03-" + "Q" * 20,
]


def test_log_token_patterns_are_the_same_as_llm_secret_patterns():
    # 片方だけに種類を足すと、ログと LLM に送る本文で伏せる範囲がずれる
    log_patterns = {rx.pattern for rx in log._SECRET_PATTERNS}
    missing = [p for p in llm.SECRET_PATTERNS if p not in log_patterns]
    assert not missing, f"log._SECRET_PATTERNS lacks llm.SECRET_PATTERNS: {missing}"


@pytest.mark.parametrize("secret", SAMPLES)
def test_redact_tokens_in_log_and_llm(secret):
    text = f"failed with key {secret} at step 2"
    assert secret not in log.redact(text)
    assert secret not in llm.Masker().text(text)


@pytest.mark.parametrize(
    "text, secret, kept",
    [
        ('{"api_key": "plain-secret-value"}', "plain-secret-value", '{"api_key": "***"}'),
        ("{'token': 'abc def'}", "abc def", "{'token': '***'}"),
        ('{"client_secret":"s3cr3t","n":1}', "s3cr3t", '{"client_secret":"***","n":1}'),
        ('{"apiKey": "k\\"q"}', 'k\\"q', '{"apiKey": "***"}'),
        ("password=hunter2 next", "hunter2", "password=*** next"),
    ],
)
def test_redact_key_value_including_json(text, secret, kept):
    out = log.redact(text)
    assert secret not in out
    assert out == kept


def test_redact_keeps_words_that_only_look_like_prefixes():
    text = "task-runner と risk-score、max_tokens: 1024"
    assert log.redact(text) == text


def test_run_main_error_output_redacts_new_patterns(capsys):
    pat = "github_pat_" + "Z" * 30
    proj = "sk-proj-" + "Ab12" * 8

    def boom(argv):
        raise RuntimeError(f'config {{"api_key": "plain-secret-value"}} {pat} {proj}')

    assert run_main(boom, []) == 3
    err = capsys.readouterr().err
    for secret in ("plain-secret-value", pat, proj):
        assert secret not in err
