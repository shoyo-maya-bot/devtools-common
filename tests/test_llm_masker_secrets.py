"""Masker: 既定で API キー・トークンらしい文字列も伏せる（secrets=False で無効）. 通信はしない.

キーの形の文字列は、リポジトリの秘密情報スキャンに引っかからないよう実行時に組み立てる（すべて架空）。
"""

from __future__ import annotations

import json

import pytest

from devtools_common.llm import MASK, SECRET_PATTERNS, LLMClient, Masker

ANTHROPIC = "sk-" + "ant-api03-" + "Ab3_" * 10
OPENAI = "sk-" + "proj-" + "Zx9" * 10
AWS = "AK" + "IA" + "ABCDEFGHIJ234567"
GH_CLASSIC = "gh" + "p_" + "a1B2" * 9
GH_FINE = "github" + "_pat_" + "11ABCDEFG0" + "x" * 30
SLACK = "xo" + "xb-" + "1234567890-abcdefABCDEF"
ALL = (ANTHROPIC, OPENAI, AWS, GH_CLASSIC, GH_FINE, SLACK)


@pytest.mark.parametrize("secret", ALL)
def test_default_masker_masks_secret_like_tokens(secret):
    m = Masker()
    assert m.text(f"key={secret} です") == f"key={MASK} です"
    assert m.text(f"「{secret}」") == f"「{MASK}」"


def test_secrets_can_be_disabled():
    m = Masker(secrets=False)
    for s in ALL:
        assert m.text(s) == s
    assert m.text("hanako@example.org") == MASK  # メール・電話はこれまでどおり


@pytest.mark.parametrize(
    "text",
    [
        "task-0123456789abcdefghijk",  # sk- が語の途中
        "risk-assessment-for-the-new-release",
        "sk-short",  # 短すぎる
        "XAKIAABCDEFGHIJ234567",  # 前に英大文字・数字が続く
        "ghp_tooshort",
    ],
)
def test_ordinary_words_are_not_masked(text):
    assert Masker().text(text) == text


def test_secret_with_digits_is_masked_whole_not_as_phone_number():
    key = "sk-" + "ant-api03-" + "09012345678" + "a" * 10
    assert Masker().text(key) == MASK


def test_client_masks_secrets_in_messages_and_system_by_default():
    c = LLMClient("claude-haiku-4-5", masker=Masker(), api_key="dummy")
    body = c._body(f"これを使って {AWS} {GH_CLASSIC}", f"鍵は {ANTHROPIC}", None, None, None, None, None, None)
    sent = json.dumps(body, ensure_ascii=False)
    for s in (AWS, GH_CLASSIC, ANTHROPIC):
        assert s not in sent


def test_secret_patterns_are_public_and_compile():
    import re

    assert SECRET_PATTERNS and all(re.compile(p) for p in SECRET_PATTERNS)
