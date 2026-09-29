"""指摘（Finding）とレポート出力の共通形式.

レビュー系・検証系ツールは Finding のリストを返し、出力は本モジュールに任せる。
形式: markdown / html / csv / json（json は AI エージェント・他ツール連携用）

JSON の形式は schemas/report.schema.json（SCHEMA_VERSION）で固定する。仕様は docs/REPORT_FORMAT.md。
互換性のない変更（キーの削除・改名・型変更）をするときは SCHEMA_VERSION のメジャーを上げる。
"""

from __future__ import annotations

import csv
import html
import io
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from enum import Enum
from importlib.resources import files
from typing import Any, TextIO

SCHEMA_VERSION = "1.0"


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def rank(self) -> int:
        return {"error": 0, "warning": 1, "info": 2}[self.value]


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: Severity
    message: str
    location: str = ""  # 例: "row 12", "sheet1!B3", "slide 4"
    field: str = ""
    expected: str = ""
    actual: str = ""
    suggestion: str = ""

    def to_dict(self) -> dict[str, str]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


COLUMNS = ["severity", "rule_id", "location", "field", "message", "expected", "actual", "suggestion"]


def summarize(findings: Iterable[Finding]) -> dict[str, int]:
    c = Counter(f.severity.value for f in findings)
    return {s.value: c.get(s.value, 0) for s in Severity}


def has_errors(findings: Iterable[Finding]) -> bool:
    return any(f.severity is Severity.ERROR for f in findings)


def _md_cell(v: str) -> str:
    return str(v).replace("|", "\\|").replace("\n", "<br>")


def report_schema() -> dict[str, Any]:
    """JSON レポートの JSON Schema（利用側の検証・AI への提示用）."""
    return json.loads(files("devtools_common").joinpath("schemas/report.schema.json").read_text(encoding="utf-8"))


def to_json_obj(
    findings: list[Finding],
    *,
    title: str,
    meta: Mapping[str, Any] | None = None,
    tool: str = "",
    tool_version: str = "",
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": tool,
        "tool_version": tool_version,
        "title": title,
        "meta": dict(meta or {}),
        "summary": summarize(findings),
        "findings": [f.to_dict() for f in findings],
    }


def render(
    findings: list[Finding],
    fmt: str,
    *,
    title: str = "Report",
    meta: Mapping[str, Any] | None = None,
    tool: str = "",
    tool_version: str = "",
) -> str:
    counts = summarize(findings)
    meta = dict(meta or {})
    if fmt == "json":
        obj = to_json_obj(findings, title=title, meta=meta, tool=tool, tool_version=tool_version)
        return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for f in findings:
            w.writerow({k: f.to_dict()[k] for k in COLUMNS})
        return buf.getvalue()
    if fmt == "markdown":
        lines = [f"# {title}", ""]
        lines += [f"- {k}: {v}" for k, v in meta.items()]
        lines += [f"- 結果: error {counts['error']} / warning {counts['warning']} / info {counts['info']}", ""]
        if not findings:
            lines.append("指摘はありません。")
        else:
            lines.append("| " + " | ".join(COLUMNS) + " |")
            lines.append("|" + "---|" * len(COLUMNS))
            for f in findings:
                d = f.to_dict()
                lines.append("| " + " | ".join(_md_cell(d[k]) for k in COLUMNS) + " |")
        return "\n".join(lines) + "\n"
    if fmt == "html":
        e = html.escape

        def row(f: Finding) -> str:
            cells = "".join(f"<td>{e(str(f.to_dict()[k]))}</td>" for k in COLUMNS)
            return f'<tr class="{e(f.severity.value)}">{cells}</tr>'

        rows = "".join(row(f) for f in findings)
        head = "".join(f"<th>{c}</th>" for c in COLUMNS)
        meta_html = "".join(f"<li>{e(str(k))}: {e(str(v))}</li>" for k, v in meta.items())
        return (
            '<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            f"<title>{e(title)}</title><style>"
            "body{font-family:sans-serif;margin:24px}table{border-collapse:collapse;width:100%}"
            "td,th{border:1px solid #ccc;padding:4px 8px;text-align:left;vertical-align:top}"
            "tr.error td:first-child{color:#b00020;font-weight:bold}tr.warning td:first-child{color:#8a6d00}"
            "</style></head><body>"
            f"<h1>{e(title)}</h1><ul>{meta_html}<li>結果: error {counts['error']} / warning {counts['warning']}"
            f" / info {counts['info']}</li></ul>"
            f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"
            "</body></html>\n"
        )
    raise ValueError(f"unknown format: {fmt}")


def write(findings: list[Finding], fmt: str, out: TextIO, **kw: Any) -> None:
    out.write(render(findings, fmt, **kw))
