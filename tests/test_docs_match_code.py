"""ドキュメント（README・docs/REPORT_FORMAT.md）と実装の値のずれを機械で見つける."""

from __future__ import annotations

import re
from pathlib import Path

from devtools_common import httpclient, report
from devtools_common.cli import ExitCode

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
REPORT_FORMAT = (ROOT / "docs" / "REPORT_FORMAT.md").read_text(encoding="utf-8")
EXIT_CODES = {int(c) for c in ExitCode} | {130}  # 130 は run_main が KeyboardInterrupt で返す


def test_report_format_exit_code_table_matches_exitcode():
    section = REPORT_FORMAT.split("## 2. 終了コード", 1)[1].split("\n## ", 1)[0]
    documented = {int(c) for c in re.findall(r"^\| `(\d+)` \|", section, re.M)}
    assert documented == EXIT_CODES


def test_readme_cli_row_lists_every_exit_code():
    row = next(ln for ln in README.splitlines() if ln.startswith("| `devtools_common.cli` |"))
    documented = {int(c) for c in re.findall(r"(\d+) [^\d/）]+", row.split("終了コード規約（", 1)[1].split("）", 1)[0])}
    assert documented == EXIT_CODES


def test_readme_idempotent_methods_match_code():
    line = next(ln for ln in README.splitlines() if "は、上のエラーでいつでも再試行する" in ln)
    documented = set(re.findall(r"\b[A-Z]{3,7}\b", line))
    assert documented == set(httpclient.IDEMPOTENT_METHODS)


def test_report_format_csv_header_matches_columns():
    assert "`" + ",".join(report.COLUMNS) + "`" in REPORT_FORMAT


def test_report_format_markdown_specials_match_code():
    para = REPORT_FORMAT.split("書式の記号（", 1)[1].split("）", 1)[0]
    documented = set(re.findall(r"`` ` ``|`([^`]+)`", para))
    documented = {d or "`" for d in documented}
    assert documented == set(report._MD_INLINE_SPECIALS)
