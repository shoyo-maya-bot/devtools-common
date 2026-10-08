"""report.to_json_obj: 同梱の report.schema.json に合う JSON を出す（合わない meta は落とす）."""

from __future__ import annotations

import json
import logging
import math

import jsonschema

from devtools_common import report
from devtools_common.report import Finding, Severity


def test_to_json_obj_drops_meta_values_the_schema_rejects(caplog):
    findings = [Finding("R-1", Severity.ERROR, "msg", location="row 1")]
    meta = {"input": "a.csv", "records": 3, "ratio": 0.5, "ok": True, "none": None, "list": [1], "map": {"a": 1}}
    meta["nan"] = math.nan
    with caplog.at_level(logging.WARNING, logger="devtools_common.report"):
        obj = report.to_json_obj(findings, title="T", meta=meta, tool="x", tool_version="1")
    jsonschema.validate(obj, report.report_schema())
    assert obj["meta"] == {"input": "a.csv", "records": 3, "ratio": 0.5, "ok": True}
    assert "none" in caplog.text and "nan" in caplog.text
    # render(json) も同じ（NaN をそのまま JSON に書かない）
    text = report.render(findings, "json", title="T", meta=meta)
    jsonschema.validate(json.loads(text), report.report_schema())
    assert "NaN" not in text


def test_to_json_obj_coerces_non_string_fields():
    f = Finding("R-1", Severity.WARNING, "msg", location=12, expected=3.5)  # type: ignore[arg-type]
    obj = report.to_json_obj([f], title=1, meta={1: "a"}, tool_version=2)  # type: ignore[arg-type]
    jsonschema.validate(obj, report.report_schema())
    assert obj["findings"][0]["location"] == "12"
    assert obj["meta"] == {"1": "a"}
