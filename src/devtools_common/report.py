"""指摘（Finding）とレポート出力の共通形式.

レビュー系・検証系ツールは Finding のリストを返し、出力は本モジュールに任せる。
形式: markdown / html / csv / json（json は AI エージェント・他ツール連携用）
CSV は表計算ソフトで開かれるため、`=` `+` `-` `@` タブ・CR で始まるセルの先頭に `'` を付ける（`neutralize_formula`）。

JSON の形式は schemas/report.schema.json（SCHEMA_VERSION）で固定する。仕様は docs/REPORT_FORMAT.md。
互換性のない変更（キーの削除・改名・型変更）をするときは SCHEMA_VERSION のメジャーを上げる。
"""

from __future__ import annotations

import csv
import html
import io
import json
import logging
import math
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from enum import Enum
from importlib.resources import files
from typing import Any, TextIO

SCHEMA_VERSION = "1.0"

_log = logging.getLogger(__name__)


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


# 表計算ソフトが数式として読む先頭の文字（CSV / 数式インジェクション）。タブ・CR も同じ扱い（OWASP の推奨）
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def neutralize_formula(value: Any) -> Any:
    """表計算ソフトで数式として実行されないよう、`=` `+` `-` `@` タブ・CR で始まる文字列の先頭に `'` を付ける.

    CSV・Excel に書き出すセルの値に使う（Excel・LibreOffice は先頭の `'` を「文字列として扱う」印として表示しない）。
    文字列以外（数値など）はそのまま返す。JSON・Markdown・HTML の出力には使わない（値を変えないため）。
    """
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


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
    """JSON レポートの dict. 必ず schemas/report.schema.json に合う形にする.

    - meta の値は文字列・数値・真偽値だけを残す。None・list・dict・NaN / 無限大などスキーマに合わない値は落とし、
      落としたキーを警告ログに出す（キーは文字列にする）
    - tool・tool_version・title と、指摘の各項目は文字列にする（数値などが渡されても型が崩れないように）
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": str(tool),
        "tool_version": str(tool_version),
        "title": str(title),
        "meta": _schema_meta(meta),
        "summary": summarize(findings),
        "findings": [{k: str(v) for k, v in f.to_dict().items()} for f in findings],
    }


def _schema_meta(meta: Mapping[str, Any] | None) -> dict[str, str | int | float | bool]:
    """meta のうちスキーマ（値は string / number / boolean）に合うものだけを返す."""
    out: dict[str, str | int | float | bool] = {}
    dropped: list[str] = []
    for k, v in (meta or {}).items():
        if isinstance(v, (str, bool, int)) or (isinstance(v, float) and math.isfinite(v)):
            out[str(k)] = v
        else:
            dropped.append(str(k))
    if dropped:
        _log.warning("report meta: dropped keys whose values are not string/number/boolean: %s", ", ".join(dropped))
    return out


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
            d = f.to_dict()
            w.writerow({k: neutralize_formula(d[k]) for k in COLUMNS})
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
