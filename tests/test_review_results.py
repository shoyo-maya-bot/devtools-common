"""devtools_common.review_results の互換テスト.

tests/data/review_results/expected_office_review.json は、office-review の main
（2e9a44ba97adeb409f8fc9c6944c1a13a55e3c34）の review_harness.results.parse / load_all と
history.load_records / resolve で同じ入力を読んだ結果（移す前の実装が正）。
入力には BOM・`\\|` のエスケープ・末尾が `\\\\|` のセル・列数違い・No が数字でない行・壊れた history の行を含める。
名前・内容はすべて架空。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devtools_common import review_results as rr

DATA = Path(__file__).parent / "data" / "review_results"
EXPECTED = json.loads((DATA / "expected_office_review.json").read_text(encoding="utf-8"))
# office-review の review_harness.checks.TYPES（type の確かめは doc_types を渡したときだけ）
OFFICE_REVIEW_TYPES = ["api", "batch", "external-if", "common", "oas", "architecture", "db"]


def _item_dict(i: rr.ReviewItem) -> dict:
    return dict(vars(i), key=i.key, fingerprint=i.fingerprint)


@pytest.mark.parametrize("name", sorted(EXPECTED["files"]))
def test_parse_matches_office_review(name):
    items, problems = rr.parse_review_file(DATA / "reviews" / name, doc_types=OFFICE_REVIEW_TYPES)
    assert [_item_dict(i) for i in items] == EXPECTED["files"][name]["items"]
    assert [p.to_dict() for p in problems] == EXPECTED["files"][name]["problems"]


def test_load_review_dir_matches_office_review():
    assert [i.key for i in rr.load_review_dir(DATA / "reviews")] == EXPECTED["load_all"]


def test_history_matches_office_review():
    warned: list[str] = []
    recs = rr.load_history(DATA / "history.jsonl", warned.append)
    assert recs == EXPECTED["history"]["records"]
    assert warned == EXPECTED["history"]["warnings"]
    res = rr.resolve_history(recs, rr.load_review_dir(DATA / "reviews"))
    assert res.decisions == EXPECTED["resolved"]["decisions"]
    assert res.orphans == EXPECTED["resolved"]["orphans"]
    assert res.all_records() == EXPECTED["resolved"]["all_records"]


def test_bom_front_matter_and_escapes():
    items, problems = rr.parse_review_file(DATA / "reviews" / "2026-10-03_stock-db_result.md")
    assert (DATA / "reviews" / "2026-10-03_stock-db_result.md").read_bytes().startswith(b"\xef\xbb\xbf")
    first = items[0]
    assert (first.target, first.doc_type, first.reviewed_at) == ("inputs/db/stock-db.md", "db", "2026-10-03")
    assert first.location == "2. テーブル | 在庫"  # \| はセルの中の |
    assert first.finding == "区分が A|B の 2 値か決まっていない"
    second = items[1]
    # \\| は「文字の \（エスケープのまま \\）」の後ろの区切り。セルの文字は \| 以外を戻さない
    assert (second.finding, second.basis, second.invalid_check) == (
        "出力先 C:\\\\data\\\\ の末尾が \\\\",
        "根拠は末尾\\\\",
        "確認\\\\",
    )
    assert items[2].finding == "索引名の<br>命名規則"  # <br> はそのまま（指紋が変わらないように）
    assert [p.location for p in problems] == ["2026-10-03_stock-db_result.md:13", "2026-10-03_stock-db_result.md:14"]


def test_unknown_type_only_with_doc_types(tmp_path):
    p = tmp_path / "x_result.md"
    p.write_text("---\ntarget: a.md\ntype: memo\nreviewed_at: 2026-10-01\n---\n", encoding="utf-8")
    assert [f.message for f in rr.parse_review_file(p)[1] if f.rule_id == "lint.meta"] == []
    msgs = [f.message for f in rr.parse_review_file(p, doc_types=["api"])[1]]
    assert "type 'memo' は不明です" in msgs


def test_split_row_and_cells():
    assert rr.split_row("| a\\\\| b |") == [" a\\\\", " b "]
    assert rr.split_row("| a\\| b |") == [" a\\| b "]
    assert rr.split_row("| a | b\\|") == [" a ", " b\\|"]  # 末尾の \| は区切りではない
    assert rr.table_cells("| x\\|y | 1 |") == ["x|y", "1"]


def test_front_matter_edge_cases():
    assert rr.front_matter("no front matter") == {}
    assert rr.front_matter("---\na: [\n---\n") == {}  # 読めない YAML
    assert rr.front_matter("---\n- a\n---\n") == {}  # mapping でない
    assert rr.front_matter("---\na: 1\n") == {}  # 閉じが無い


def test_fingerprint_normalization():
    a = rr.fingerprint("t.md", "3. 処理", "ＡＢＣ  の 件数")
    assert a == rr.fingerprint("t.md", " 3.  処理 ", "abc の 件数")
    assert a != rr.fingerprint("t.md", "3. 処理", "別の指摘")
    assert rr.normalize("Ａ\u3000B") == "a b"


def test_history_missing_file_and_default_warn(tmp_path, capsys):
    assert rr.load_history(tmp_path / "none.jsonl") == []
    h = tmp_path / "history.jsonl"
    h.write_text("{ broken\n", encoding="utf-8")
    assert rr.load_history(h) == []
    assert "history.jsonl:1" in capsys.readouterr().err


def test_legacy_record_is_later_overridden(tmp_path):
    items = rr.load_review_dir(DATA / "reviews")
    it = items[0]
    recs = [
        {"key": it.key, "decision": "rejected", "check_id": it.check_id, "finding": it.finding},
        {"key": it.key, "fingerprint": it.fingerprint, "decision": "adopted", "check_id": it.check_id},
    ]
    res = rr.resolve_history(recs, items)
    assert res.decisions[it.fingerprint]["decision"] == "adopted"
    assert res.orphans == []
