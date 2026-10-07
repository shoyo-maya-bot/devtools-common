"""Excel の読み取りの共通部品（doc-to-markdown・design-diff などで共有）.

| 関数・クラス | 内容 |
|---|---|
| `render_value(値, 表示形式)` | 表示形式（ゼロ埋め・桁区切り・%・通貨・日付・和暦 等）どおりの文字列と、再現できたか |
| `render_cell(cell)` | openpyxl のセルを `render_value` で文字列にする |
| `MergeMap` / `Merge` | 結合範囲の索引（左上の値で埋める・覆われたセルの判定） |
| `header_depth` / `header_names` | 複数段の見出しの段数（結合セルから判断、`header_rows` で指定可）と「親/子」の列名 |
| `looks_numeric` | 表示形式付きの文字列（`1,500` `12%` `¥300`）が数値に見えるか |
| `load_workbook_with_formula_fallback(path)` | 計算結果で読み、計算結果の無い数式セルには数式の文字列を入れる |

`render_value` / `MergeMap` / 見出しの判定は純粋な関数で、openpyxl を import しない.
ブックの読み込みには openpyxl が必要（`pip install "devtools-common[excel] @ git+…"`）.
無いときは入れ方を書いた ImportError.
"""

from __future__ import annotations

from typing import Any

from .merged import MAX_HEADER_ROWS, CellText, Merge, MergeMap, header_depth, header_names, looks_numeric
from .numfmt import LOCALE_DEFAULT, Unsupported, general, render_value
from .workbook import load_workbook_with_formula_fallback, require_openpyxl


def render_cell(cell: Any) -> tuple[str, bool]:
    """openpyxl のセル（value と number_format を持つもの）を表示形式どおりの文字列にする."""
    return render_value(cell.value, getattr(cell, "number_format", "") or "")


__all__ = [
    "LOCALE_DEFAULT",
    "MAX_HEADER_ROWS",
    "CellText",
    "Merge",
    "MergeMap",
    "Unsupported",
    "general",
    "header_depth",
    "header_names",
    "load_workbook_with_formula_fallback",
    "looks_numeric",
    "render_cell",
    "render_value",
    "require_openpyxl",
]
