"""採点で再現した不具合の回帰テスト（ログの書式・URL の組み立て・CSV の数式・Retry-After・応答の記録）.

通信はしない（opener の差し替えと FakeLLM だけを使う）。名前・アドレスはすべて架空。
"""

from __future__ import annotations

import csv
import io
import json
import logging
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path

from devtools_common import log as dlog
from devtools_common import report
from devtools_common.httpclient import HttpClient, HttpError
from devtools_common.llm import MASK, FakeLLM, Masker
from devtools_common.report import Finding, Severity


def _handler_with_filter() -> tuple[logging.Logger, io.StringIO, logging.Handler]:
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter("%(message)s"))
    h.addFilter(dlog.RedactFilter())
    lg = logging.getLogger("devtools_common.test_hardening")
    lg.handlers[:] = [h]
    lg.propagate = False
    lg.setLevel(logging.DEBUG)
    return lg, buf, h


class RedactFilterFormatTest(unittest.TestCase):
    """RedactFilter が %d・%.1f・dict 引数のログを壊さない.

    以前は TypeError で「--- Logging error ---」になり、ログが消えていた。
    """

    def setUp(self):
        self.lg, self.buf, self.h = _handler_with_filter()
        # logging.raiseExceptions のときは、書式の誤りが標準エラーに「--- Logging error ---」として出る。それを捕まえる
        self.errors: list[logging.LogRecord] = []
        self.h.handleError = self.errors.append  # type: ignore[method-assign]

    def test_int_and_float_args(self):
        self.lg.info("retrying %s %s (attempt %d) in %.1fs", "GET", "/items", 2, 1.5)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.buf.getvalue(), "retrying GET /items (attempt 2) in 1.5s\n")

    def test_dict_arg(self):
        self.lg.info("model=%(model)s tokens=%(n)d cost=%(c).4f", {"model": "m", "n": 12, "c": 0.5})
        self.assertEqual(self.errors, [])
        self.assertEqual(self.buf.getvalue(), "model=m tokens=12 cost=0.5000\n")

    def test_secret_in_args_is_redacted(self):
        self.lg.warning("auth %s (%d)", "Bearer abcdefghijklmnopqrstuvwxyz", 3)
        self.assertEqual(self.errors, [])
        out = self.buf.getvalue()
        self.assertNotIn("abcdefghijklmnop", out)
        self.assertIn("Bearer ***", out)
        self.assertIn("(3)", out)

    def test_record_is_formatted_once(self):
        rec = logging.LogRecord("x", logging.INFO, __file__, 1, "n=%d", (5,), None)
        dlog.RedactFilter().filter(rec)
        self.assertEqual(rec.args, ())
        self.assertEqual(rec.getMessage(), "n=5")

    def test_broken_format_is_not_dropped(self):
        """書式と引数が合わないログも、黙って消さずに中身が分かる形で残す."""
        self.lg.info("only one %s", "a", "b")
        self.assertEqual(self.errors, [])
        self.assertIn("only one %s", self.buf.getvalue())


class _Resp:
    status = 200
    headers: dict[str, str] = {}

    def read(self):
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None


class HttpParamsTest(unittest.TestCase):
    def _url(self, path, params):
        seen = []

        def opener(req, timeout):
            seen.append(req.full_url)
            return _Resp()

        HttpClient("https://api.example.test/v1", opener=opener, rate_per_sec=1000).get(path, params=params)
        return seen[0]

    def test_params_appended_to_existing_query(self):
        self.assertEqual(self._url("items?page=2", {"q": "x"}), "https://api.example.test/v1/items?page=2&q=x")

    def test_params_without_query(self):
        self.assertEqual(
            self._url("items", {"q": "a b", "t": ["1", "2"]}), "https://api.example.test/v1/items?q=a+b&t=1&t=2"
        )

    def test_existing_query_kept_and_fragment_dropped_from_query(self):
        self.assertEqual(self._url("items?", {"q": "x"}), "https://api.example.test/v1/items?q=x")


class RetryAfterNegativeTest(unittest.TestCase):
    def test_negative_retry_after_is_clamped_to_zero(self):
        slept: list[float] = []
        calls = []

        def opener(req, timeout):
            calls.append(1)
            if len(calls) == 1:
                hdrs = Message()
                hdrs["Retry-After"] = "-5"
                raise urllib.error.HTTPError(req.full_url, 503, "busy", hdrs, io.BytesIO(b""))
            return _Resp()

        c = HttpClient("https://api.example.test", opener=opener, sleep=slept.append, rate_per_sec=1000)
        self.assertEqual(c.get("x").status, 200)
        self.assertEqual(slept[0], 0.0)  # 再試行の待ち（その後はレート制限の待ち）
        self.assertTrue(all(s >= 0 for s in slept))

    def test_backoff_never_negative(self):
        c = HttpClient("https://api.example.test")
        self.assertEqual(c._backoff(0, "-5"), 0.0)
        self.assertEqual(c._backoff(0, "nan-ish"), c.backoff_base)

    def test_retry_after_nan_or_inf(self):
        c = HttpClient("https://api.example.test")
        self.assertEqual(c._backoff(0, "inf"), c.backoff_max)
        self.assertEqual(c._backoff(1, "nan"), c.backoff_base * 2)

    def test_all_failures_raise_http_error(self):
        def opener(req, timeout):
            hdrs = Message()
            hdrs["Retry-After"] = "-5"
            raise urllib.error.HTTPError(req.full_url, 503, "busy", hdrs, io.BytesIO(b""))

        c = HttpClient(
            "https://api.example.test", opener=opener, sleep=lambda s: None, rate_per_sec=1000, max_retries=1
        )
        with self.assertRaises(HttpError):
            c.get("x")


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


def _msg(text):
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-4-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _tool(data):
    m = _msg("")
    m["content"] = [{"type": "tool_use", "id": "toolu_1", "name": "record", "input": data}]
    m["stop_reason"] = "tool_use"
    return m


class RecordMaskTest(unittest.TestCase):
    """record_path に応答本文を伏せ字なしで書き出していた."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rec = Path(self.tmp.name) / "r.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def _lines(self):
        return [json.loads(x) for x in self.rec.read_text(encoding="utf-8").splitlines()]

    def test_masker_applied_to_record_by_default(self):
        fake = FakeLLM(
            [_msg("連絡先 hanako@example.org"), _tool({"who": "hanako@example.org", "n": 1})],
            record_path=self.rec,
            masker=Masker(),
        )
        self.assertIn("hanako@example.org", fake.complete("a").text)  # 戻り値は伏せない
        fake.complete("b")
        text = self.rec.read_text(encoding="utf-8")
        self.assertNotIn("hanako@example.org", text)
        lines = self._lines()
        self.assertEqual(lines[0]["content"][0]["text"], f"連絡先 {MASK}")
        self.assertEqual(lines[1]["content"][0]["input"], {"who": MASK, "n": 1})

    def test_record_content_omit(self):
        fake = FakeLLM([_msg("秘密の本文"), _tool({"who": "x"})], record_path=self.rec, record_content="omit")
        fake.complete("a")
        fake.complete("b")
        text = self.rec.read_text(encoding="utf-8")
        self.assertNotIn("秘密の本文", text)
        lines = self._lines()
        self.assertEqual(lines[0]["content"][0]["text"], "<text 5 chars>")
        self.assertEqual(lines[1]["content"][0]["input"], {})
        self.assertEqual(lines[0]["usage"], {"input_tokens": 1, "output_tokens": 1})

    def test_record_content_raw_keeps_body(self):
        fake = FakeLLM([_msg("a@example.org")], record_path=self.rec, masker=Masker(), record_content="raw")
        fake.complete("a")
        self.assertIn("a@example.org", self.rec.read_text(encoding="utf-8"))

    def test_record_content_auto_without_masker_is_raw(self):
        fake = FakeLLM([_msg("本文")], record_path=self.rec)
        fake.complete("a")
        self.assertEqual(self._lines()[0]["content"][0]["text"], "本文")

    def test_masked_without_masker_is_error(self):
        with self.assertRaises(ValueError):
            FakeLLM([], record_path=self.rec, record_content="masked")

    def test_unknown_mode_is_error(self):
        with self.assertRaises(ValueError):
            FakeLLM([], record_path=self.rec, record_content="secret")


if __name__ == "__main__":
    unittest.main()
