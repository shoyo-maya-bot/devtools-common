"""AI レビュー結果（reviews/*_result.md）と判定の履歴（history.jsonl）の読み込み.

office-review（review_harness）が書き、office-review と quality-analytics が読む形式を、1 か所で読む共通部品。
office-review の review_harness/results.py・history.py（と precheck.front_matter、field_validation.mdtable.split_row）
から移した。規則を変えるときは両ツールの互換テストを合わせて直す。

レビュー結果ファイルの形式（UTF-8。BOM 付きでもよい）:

    ---
    target: inputs/api/order-api.md
    type: api
    reviewed_at: 2026-10-02
    ---
    # レビュー結果: order-api

    | No | check_id | 重要度 | ロール | 箇所 | 指摘 | 根拠 | 無効となるケースの確認 |
    |---|---|---|---|---|---|---|---|
    | 1 | API-002 | MUST | spec_manager | 3. 入力 | … | … | 入力ありのため該当しない |

- セルの区切りは、エスケープされていない `|`。`\\|` はセルの中の `|`、`\\\\|` は「文字の `\\`」の後ろの区切り
- セルの文字は `\\|` だけを `|` に戻す（指紋が変わらないよう、ほかのエスケープ・`<br>` はそのまま残す）
- check_id が NEW の指摘は「既存の観点に無い指摘」

history.jsonl の形式（1 行 1 判定、後の行が有効）:

    {"key": "<ファイル名>#<No>", "fingerprint": "fp:…", "decision": "adopted" | "rejected", "check_id": "…", ...}

旧形式の行（fingerprint なし）は、その No にいまある指摘が同じとき（本文と check_id が一致）だけ結び付ける。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sys
import unicodedata
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .report import Finding, Severity

__all__ = [
    "COLUMNS",
    "HISTORY_DECISIONS",
    "HISTORY_REQUIRED_KEYS",
    "REQUIRED_META",
    "ResolvedHistory",
    "ReviewItem",
    "fingerprint",
    "front_matter",
    "load_history",
    "load_review_dir",
    "normalize",
    "parse_review_file",
    "resolve_history",
    "split_row",
    "table_cells",
]

COLUMNS = ["No", "check_id", "重要度", "ロール", "箇所", "指摘", "根拠", "無効となるケースの確認"]
REQUIRED_META = ["target", "type", "reviewed_at"]
HISTORY_DECISIONS = frozenset({"adopted", "rejected"})
HISTORY_REQUIRED_KEYS = ("decision", "check_id")


# ---------------------------------------------------------------- 指紋


def normalize(text: str) -> str:
    """指紋用の正規化: 全角・半角の統一（NFKC）、空白の連続を 1 つに、前後の空白を除く、英字を小文字に."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().lower()


def fingerprint(target: str, location: str, finding: str) -> str:
    """指摘の指紋（"fp:" + 16 桁の 16 進）. 対象（空ならファイル名を渡す）・箇所・指摘の本文から作る."""
    parts = [normalize(target), normalize(location), normalize(finding)]
    return "fp:" + hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


@dataclass
class ReviewItem:
    """レビュー結果の表の 1 行（指摘 1 件）."""

    file: str
    target: str
    doc_type: str
    reviewed_at: str
    no: int
    check_id: str
    severity: str
    role: str
    location: str
    finding: str
    basis: str
    invalid_check: str

    @property
    def key(self) -> str:
        """ファイル名 + 番号（旧形式のキー）.

        再レビューで No がずれると別の指摘を指すので、判定の結び付けには使わない。
        """
        return f"{self.file}#{self.no}"

    @property
    def fingerprint(self) -> str:
        """指摘の指紋. 対象・箇所・指摘の本文から作り、No やファイル名（レビュー日）が変わっても同じ値になる."""
        return fingerprint(self.target or self.file, self.location, self.finding)


# ---------------------------------------------------------------- Front Matter・表


def front_matter(text: str) -> dict[str, Any]:
    """先頭の `---` 〜 `---` の YAML. 無い・読めない・mapping でないときは空の dict.

    BOM は呼び出し側で外す（ファイルは encoding="utf-8-sig" で読む）。
    """
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    try:
        data = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def split_row(line: str) -> list[str]:
    """Markdown（GFM）の表の 1 行をセル（エスケープはそのまま）に分ける. 先頭・末尾の `|` は除く.

    区切りはエスケープされていない `|`。`\\` の後ろの 1 文字はセルの一部（`\\|` は区切りではなく、`\\\\|` は区切り）。
    """
    s = line.strip()
    cells: list[str] = []
    cur: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            cur.append(s[i : i + 2])
            i += 2
            continue
        if ch == "|":
            cells.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    cells.append("".join(cur))
    if s.startswith("|"):
        cells = cells[1:]
    if s.endswith("|") and not _escaped_at_end(s) and cells:
        cells = cells[:-1]
    return cells


def _escaped_at_end(s: str) -> bool:
    """末尾の | が \\ でエスケープされているか（直前の \\ の数が奇数）."""
    n = 0
    for ch in reversed(s[:-1]):
        if ch != "\\":
            break
        n += 1
    return n % 2 == 1


def table_cells(line: str) -> list[str]:
    """レビュー結果の表の 1 行 → セルの文字（前後の空白を除き、`\\|` だけを `|` に戻す）."""
    return [c.strip().replace("\\|", "|") for c in split_row(line)]


# ---------------------------------------------------------------- レビュー結果ファイル


def parse_review_file(
    path: str | Path, *, doc_types: Collection[str] | None = None
) -> tuple[list[ReviewItem], list[Finding]]:
    """レビュー結果 1 ファイル → (指摘の行, 形式の誤り).

    形式の誤り（rule_id `lint.meta` / `lint.table`）: Front Matter の target・type・reviewed_at が無い、
    type が doc_types に無い（doc_types を渡したときだけ確かめる）、指摘の表（列: COLUMNS）が無い、
    行の列数が違う・No が数字でない（その行は飛ばす）。表は見出しの次の区切り行の後から、`|` で始まらない行の前まで。
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    meta = front_matter(text)
    problems: list[Finding] = []
    loc = path.name
    for k in REQUIRED_META:
        if not meta.get(k):
            problems.append(Finding("lint.meta", Severity.ERROR, f"Front Matter に {k} がありません", location=loc))
    if doc_types is not None and meta.get("type") and meta["type"] not in doc_types:
        problems.append(Finding("lint.meta", Severity.ERROR, f"type '{meta['type']}' は不明です", location=loc))
    lines = text.splitlines()
    header_at = next((i for i, ln in enumerate(lines) if ln.startswith("|") and table_cells(ln) == COLUMNS), None)
    if header_at is None:
        problems.append(
            Finding(
                "lint.table", Severity.ERROR, "指摘の表（列: " + " / ".join(COLUMNS) + "）がありません", location=loc
            )
        )
        return [], problems
    items: list[ReviewItem] = []
    reviewed = meta.get("reviewed_at", "")
    if isinstance(reviewed, dt.date):
        reviewed = reviewed.isoformat()
    for i in range(header_at + 2, len(lines)):
        ln = lines[i]
        if not ln.startswith("|"):
            break
        cells = table_cells(ln)
        if len(cells) != len(COLUMNS):
            problems.append(
                Finding("lint.table", Severity.ERROR, f"列数が {len(cells)} です", location=f"{loc}:{i + 1}")
            )
            continue
        try:
            no = int(cells[0])
        except ValueError:
            problems.append(Finding("lint.table", Severity.ERROR, "No が数字ではありません", location=f"{loc}:{i + 1}"))
            continue
        items.append(
            ReviewItem(path.name, str(meta.get("target", "")), str(meta.get("type", "")), str(reviewed), no, *cells[1:])
        )
    return items, problems


def load_review_dir(reviews_dir: str | Path, pattern: str = "*_result.md") -> list[ReviewItem]:
    """フォルダのレビュー結果（ファイル名順）の指摘をすべて読む.

    形式の誤りの行は飛ばす（誤りを確かめるのは parse_review_file）。
    """
    items: list[ReviewItem] = []
    for p in sorted(Path(reviews_dir).glob(pattern)):
        got, _ = parse_review_file(p)
        items += got
    return items


# ---------------------------------------------------------------- 判定の履歴（history.jsonl）


def load_history(path: str | Path, warn: Callable[[str], Any] | None = None) -> list[dict[str, Any]]:
    """history.jsonl の全行（書いた順）. ファイルが無ければ空.

    壊れた行（JSON として読めない・オブジェクトでない・decision（adopted / rejected）や check_id が無い）は、
    「警告: history.jsonl:<行番号> を飛ばしました（理由）」を warn に渡して飛ばす
    （1 行の破損で全体が止まらないように）。
    warn の既定は標準エラーへの出力。UTF-8（BOM 付きでもよい）。
    """
    path = Path(path)
    if not path.is_file():
        return []
    warn = warn or (lambda msg: print(msg, file=sys.stderr))
    out: list[dict[str, Any]] = []
    for no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            warn(f"警告: {path.name}:{no} を飛ばしました（JSON として読めません: {e.msg}）")
            continue
        if (
            not isinstance(rec, dict)
            or any(k not in rec for k in HISTORY_REQUIRED_KEYS)
            or rec["decision"] not in HISTORY_DECISIONS
        ):
            warn(f"警告: {path.name}:{no} を飛ばしました（decision（adopted / rejected）と check_id が必要です）")
            continue
        out.append(rec)
    return out


@dataclass
class ResolvedHistory:
    """履歴を、いまの指摘に結び付けた結果."""

    decisions: dict[str, dict[str, Any]] = field(default_factory=dict)  # 指紋 → 最新の判定
    orphans: list[dict[str, Any]] = field(default_factory=list)  # どの指摘にも結び付けられない旧形式の判定

    def all_records(self) -> dict[str, dict[str, Any]]:
        """集計用: 指紋ごとの最新の判定 + 孤立した旧形式の判定（旧 key ごとに最新）."""
        out = dict(self.decisions)
        out.update({"legacy:" + r["key"]: r for r in self.orphans})
        return out


def _legacy_matches(rec: dict[str, Any], item: ReviewItem) -> bool:
    """旧形式（key = ファイル名#No）の判定が、いまその No にある指摘と同じものか（本文と check_id が一致）."""
    return normalize(str(rec.get("finding", ""))) == normalize(item.finding) and rec.get("check_id") == item.check_id


def resolve_history(records: Iterable[dict[str, Any]], items: Iterable[ReviewItem]) -> ResolvedHistory:
    """判定を指紋で引けるようにする. 同じ指紋は後に書いたものが勝つ.

    - fingerprint がある行: その指紋の判定
    - 旧形式の行（fingerprint なし）: key の No にいまある指摘が同じ指摘のときだけ、その指紋に引き継ぐ。
      違う指摘になっていれば（再レビューで No がずれた等）孤立した判定（orphans）として返す
    """
    by_key = {i.key: i for i in items}
    res = ResolvedHistory()
    orphans: dict[str, dict[str, Any]] = {}
    for rec in records:
        fp = rec.get("fingerprint")
        if not fp:
            item = by_key.get(rec.get("key", ""))
            if item is None or not _legacy_matches(rec, item):
                orphans[rec.get("key", "")] = rec
                continue
            fp = item.fingerprint
            orphans.pop(rec.get("key", ""), None)
        res.decisions[fp] = {**rec, "fingerprint": fp}
    res.orphans = list(orphans.values())
    return res
