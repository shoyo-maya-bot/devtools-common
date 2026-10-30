"""Masker の電話番号の判定（区切りの無い数字の列を注文番号などと区別する）. 番号はすべて架空."""

from __future__ import annotations

import pytest

from devtools_common.llm import MASK, Masker


@pytest.mark.parametrize(
    "text",
    [
        "ID 0123456789 の注文",
        "注文番号 0312345678 を出荷",
        "伝票 00012345678",
        "請求書 ０１２３４５６７８９ を確認",
        "コード0120123456",
    ],
)
def test_bare_digit_ids_are_kept(text):
    assert Masker().text(text) == text


@pytest.mark.parametrize(
    "text, expected",
    [
        ("携帯 09012345678", f"携帯 {MASK}"),
        ("050 番号 05012345678", f"050 番号 {MASK}"),
        ("電話: 0312345678", f"電話: {MASK}"),
        ("TEL 0612345678 まで", f"TEL {MASK} まで"),
        ("連絡先は0312345678です", f"連絡先は{MASK}です"),
        ("Phone: ０３１２３４５６７８", f"Phone: {MASK}"),
        ("ID 03-1234-5678", f"ID {MASK}"),  # 区切り付きは周りの語に関係なく伏せる
        ("+81312345678", MASK),
    ],
)
def test_phone_numbers_are_masked(text, expected):
    assert Masker().text(text) == expected


def test_context_word_must_be_near():
    text = "電話の件は別途。なお今回の注文の ID は 0312345678 です"
    assert Masker().text(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "比率 0.1234567890",
        "係数 0.123456789",
        "値は 0.5 0.25 0.125 0.0625",
        "1.0312345678 倍",
        "全角 ０．１２３４５６７８９０",
    ],
)
def test_decimals_are_not_masked_as_phone_numbers(text):
    assert Masker().text(text) == text


@pytest.mark.parametrize(
    "text, expected",
    [
        ("FMC 06012345678", f"FMC {MASK}"),  # README に書いた 060 も区切り無しで伏せる
        ("03.1234.5678", MASK),  # 小数点を区切りにした電話番号は伏せる
        ("電話 0.3123.45678 は誤記", "電話 0.3123.45678 は誤記"),  # 「0.」で始まるものは小数とみなす
    ],
)
def test_phone_number_edge_cases(text, expected):
    assert Masker().text(text) == expected
