"""report の Markdown: 表のセルの CR、表題と meta の改行・書式の記号."""

from __future__ import annotations

from devtools_common import report
from devtools_common.report import Finding, Severity


def test_cell_cr_and_crlf_become_br():
    assert report._md_cell("a\rb\r\nc") == "a<br>b<br>c"
    md = report.render([Finding("r", Severity.ERROR, "x\ry")], "markdown")
    assert "\r" not in md


def test_title_and_meta_cannot_break_out_of_their_line():
    md = report.render(
        [],
        "markdown",
        title="t\n# injected\r<script>x</script>",
        meta={"in\nput": "[link](javascript:alert(1)) *b* `c`"},
    )
    lines = md.splitlines()
    assert lines[0] == "# t # injected \\<script\\>x\\</script\\>"
    assert not any(ln.startswith("# injected") for ln in lines)
    assert "- in put: \\[link\\](javascript:alert(1)) \\*b\\* \\`c\\`" in lines
    assert "<script>" not in md


def test_plain_title_is_unchanged():
    md = report.render([], "markdown", title="検証レポート 2026-10-23", meta={"input": "a.csv", "records": 3})
    assert md.startswith("# 検証レポート 2026-10-23\n\n- input: a.csv\n- records: 3\n")
