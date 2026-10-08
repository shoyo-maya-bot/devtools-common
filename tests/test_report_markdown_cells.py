"""report の Markdown 表のセル: `\\` と `|` をエスケープし、表の読み取りで同じ値・同じ列数に戻ること."""

from __future__ import annotations

import pytest

from devtools_common import report
from devtools_common import review_results as rr
from devtools_common.report import Finding, Severity

VALUES = ["a\\|b", "a\\", "\\\\server\\share", "x|y", "|", "\\", "a\\\\|b", "末尾\\|", "C:\\data\\"]


@pytest.mark.parametrize("value", VALUES)
def test_markdown_cell_round_trip(value):
    f = Finding("r.1", Severity.ERROR, value, location=value)
    md = report.render([f], "markdown", title="t")
    row = [ln for ln in md.splitlines() if ln.startswith("| ")][-1]
    cells = rr.split_row(row)
    assert len(cells) == len(report.COLUMNS)  # 列がずれない
    got = dict(zip(report.COLUMNS, (rr.unescape_cell(c.strip()) for c in cells), strict=True))
    assert got["message"] == value and got["location"] == value


def test_backslash_before_pipe_does_not_split():
    row = "| " + report._md_cell("a\\|b") + " | z |"
    assert rr.table_cells(row)[1] == "z"
    assert len(rr.table_cells(row)) == 2


def test_newline_still_becomes_br():
    assert report._md_cell("a\nb") == "a<br>b"
