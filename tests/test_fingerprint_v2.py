"""指紋 v2（対象・箇所・本文・ロール・check_id）と、v2 での判定の結び付け.

tests/data/review_results_v2/expected_office_review_v2.json は、office-review の main
（424c01be3fd54913f7a62ed24da0efe287b69b34）の review_harness.results.load_all と history.load_records / resolve で
同じ入力を読んだ結果（office-review の実装が正）。入力には、旧指紋が同じでロール・観点が違う 2 件、v2 付きの行、
旧指紋だけの行（1 件に決まる・ロールと観点で決まる・決まらない）、key だけの行（一致・不一致）、
いまに無い旧指紋を含める。
"""

from __future__ import annotations

import json
from pathlib import Path

from devtools_common import review_results as rr

DATA = Path(__file__).parent / "data" / "review_results_v2"
EXPECTED = json.loads((DATA / "expected_office_review_v2.json").read_text(encoding="utf-8"))


def test_item_fingerprints_match_office_review():
    items = rr.load_review_dir(DATA / "reviews")
    got = [{"key": i.key, "fingerprint_v1": i.fingerprint_v1, "fingerprint_v2": i.fingerprint_v2} for i in items]
    assert got == EXPECTED["items"]
    # 既定の fingerprint は今までどおり v1（既存の利用側の値を変えない）
    assert all(i.fingerprint == i.fingerprint_v1 for i in items)


def test_resolve_history_v2_matches_office_review():
    items = rr.load_review_dir(DATA / "reviews")
    recs = rr.load_history(DATA / "history.jsonl", lambda m: None)
    res = rr.resolve_history_v2(recs, items)
    assert res.decisions == EXPECTED["decisions"]
    assert res.orphans == EXPECTED["orphans"]
    assert res.all_records() == EXPECTED["all_records"]


def test_v2_separates_roles_that_v1_merges():
    items = rr.load_review_dir(DATA / "reviews")
    a, b = items[0], items[1]  # 同じ箇所・同じ本文、ロールと観点が違う
    assert a.fingerprint_v1 == b.fingerprint_v1
    assert a.fingerprint_v2 != b.fingerprint_v2
    recs = [
        {
            "key": a.key,
            "fingerprint": a.fingerprint_v1,
            "fingerprint_v2": a.fingerprint_v2,
            "decision": "adopted",
            "check_id": a.check_id,
        },
        {
            "key": b.key,
            "fingerprint": b.fingerprint_v1,
            "fingerprint_v2": b.fingerprint_v2,
            "decision": "rejected",
            "check_id": b.check_id,
        },
    ]
    v2 = rr.resolve_history_v2(recs, items).decisions
    assert (v2[a.fingerprint_v2]["decision"], v2[b.fingerprint_v2]["decision"]) == ("adopted", "rejected")
    # v1 では同じ指紋になり、後の行だけが残る（従来の resolve_history の振る舞いは変えない）
    v1 = rr.resolve_history(recs, items).decisions
    assert v1[a.fingerprint_v1]["decision"] == "rejected"


def test_fingerprint_v2_function():
    v = rr.fingerprint_v2("t.md", "3. 処理", "ＡＢＣ  の 件数", "Tester", "API-001")
    assert v == rr.fingerprint_v2("t.md", " 3.  処理 ", "abc の 件数", "tester", "api-001")
    assert v != rr.fingerprint_v2("t.md", "3. 処理", "abc の 件数", "dba", "API-001")
    assert v != rr.fingerprint("t.md", "3. 処理", "abc の 件数")
    assert v.startswith("fp:") and len(v) == 19


def test_office_review_style_subclass_keeps_working():
    # office-review は ReviewItem を継承して fingerprint を v2 に替えている。その形でも v1・v2 が正しい値になる
    class Sub(rr.ReviewItem):
        @property
        def fingerprint(self) -> str:
            return self.fingerprint_v2

    base = rr.load_review_dir(DATA / "reviews")[0]
    sub = Sub(**vars(base))
    assert sub.fingerprint == base.fingerprint_v2
    assert sub.fingerprint_v1 == base.fingerprint_v1
    # resolve_history に Sub を渡すと、従来どおり item.fingerprint（ここでは v2）で結び付く
    rec = {"key": sub.key, "decision": "adopted", "check_id": sub.check_id, "finding": sub.finding}
    assert list(rr.resolve_history([rec], [sub]).decisions) == [sub.fingerprint_v2]
