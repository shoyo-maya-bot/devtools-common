"""report: CSV に書くセルを数式として実行されない形にする（JSON は変えない）."""

from __future__ import annotations

import csv
import io
import json
import unittest

from devtools_common import report
from devtools_common.report import Finding, Severity


class CsvFormulaTest(unittest.TestCase):
    def test_neutralize_formula(self):
        n = report.neutralize_formula
        for v in ("=1+1", "+1", "-1", "@SUM(A1)", "\tx", "\rx", '=HYPERLINK("http://x.test")'):
            with self.subTest(v=v):
                self.assertEqual(n(v), "'" + v)
        for v in ("", "abc", "合計 = 3", "1-2", "'=already"):
            with self.subTest(v=v):
                self.assertEqual(n(v), v)

    def test_csv_cells_are_neutralized(self):
        f = Finding("R=1", Severity.ERROR, '=HYPERLINK("http://evil.test","x")', "@A1", "+f", "-1", "=2", "\tz")
        rows = list(csv.DictReader(io.StringIO(report.render([f], "csv"))))
        self.assertEqual(rows[0]["message"], '\'=HYPERLINK("http://evil.test","x")')
        self.assertEqual(rows[0]["location"], "'@A1")
        self.assertEqual(rows[0]["field"], "'+f")
        self.assertEqual(rows[0]["expected"], "'-1")
        self.assertEqual(rows[0]["actual"], "'=2")
        self.assertEqual(rows[0]["suggestion"], "'\tz")
        self.assertEqual(rows[0]["rule_id"], "R=1")  # 先頭でなければそのまま
        self.assertEqual(rows[0]["severity"], "error")

    def test_json_is_not_changed(self):
        f = Finding("R1", Severity.ERROR, "=1+1")
        self.assertEqual(json.loads(report.render([f], "json"))["findings"][0]["message"], "=1+1")


if __name__ == "__main__":
    unittest.main()
