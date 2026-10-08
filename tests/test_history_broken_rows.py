"""history.jsonl の壊れた行（型の違い・識別子の欠落）を、全体を止めずに警告して飛ばすことの確認. 内容はすべて架空."""

from __future__ import annotations

import json

import pytest

from devtools_common import review_results as rr


def _write(tmp_path, rows):
    p = tmp_path / "history.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return p


GOOD = {"key": "a_result.md#1", "fingerprint": "fp:1111111111111111", "decision": "adopted", "check_id": "API-001"}


@pytest.mark.parametrize(
    "bad",
    [
        {**GOOD, "decision": ["adopted"]},  # list は hash できず `in frozenset` で TypeError になっていた
        {**GOOD, "decision": {"v": "adopted"}},
        {**GOOD, "check_id": ["API-001"]},
        {**GOOD, "check_id": 1},
        {**GOOD, "fingerprint": ["fp:1"]},
        {**GOOD, "fingerprint_v2": 123},
        {**GOOD, "key": None},
    ],
)
def test_non_string_fields_are_skipped_with_warning(tmp_path, bad):
    warned: list[str] = []
    recs = rr.load_history(_write(tmp_path, [bad, GOOD]), warned.append)
    assert recs == [GOOD]
    assert len(warned) == 1 and warned[0].startswith("警告: history.jsonl:1 を飛ばしました（")
    assert "文字列ではありません" in warned[0]


def test_row_without_any_identifier_is_skipped(tmp_path):
    bad = {"decision": "adopted", "check_id": "API-001"}
    empty = {"key": "", "fingerprint": "", "decision": "rejected", "check_id": "API-001"}
    warned: list[str] = []
    recs = rr.load_history(_write(tmp_path, [bad, empty, GOOD]), warned.append)
    assert recs == [GOOD]
    assert warned == [
        "警告: history.jsonl:1 を飛ばしました（fingerprint_v2・fingerprint・key のどれかが必要です）",
        "警告: history.jsonl:2 を飛ばしました（fingerprint_v2・fingerprint・key のどれかが必要です）",
    ]


def test_loaded_rows_never_break_resolve_or_all_records(tmp_path):
    rows = [{"decision": "adopted", "check_id": "API-001"}, {**GOOD, "decision": ["x"]}, GOOD]
    recs = rr.load_history(_write(tmp_path, rows), lambda m: None)
    for resolve in (rr.resolve_history, rr.resolve_history_v2):
        res = resolve(recs, [])
        assert list(res.all_records()) == ["fp:1111111111111111"]


def test_all_records_tolerates_orphan_without_key():
    # load_history を通さずに渡された行でも KeyError にしない
    res = rr.resolve_history([{"decision": "adopted", "check_id": "X"}], [])
    assert res.all_records() == {"legacy:": {"decision": "adopted", "check_id": "X"}}
