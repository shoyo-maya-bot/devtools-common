from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path

from devtools_common import cli, config, log, report, tempfiles
from devtools_common.httpclient import HttpClient, HttpError, RateLimiter
from devtools_common.report import Finding, Severity


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.findings = [
            Finding("R1", Severity.ERROR, "合計が一致しません", "row 2", "total", "30", "31", "内訳を確認"),
            Finding("R2", Severity.WARNING, "a|b", "row 3"),
        ]

    def test_summary_and_errors(self):
        self.assertEqual(report.summarize(self.findings), {"error": 1, "warning": 1, "info": 0})
        self.assertTrue(report.has_errors(self.findings))
        self.assertFalse(report.has_errors(self.findings[1:]))

    def test_json_roundtrip(self):
        data = json.loads(report.render(self.findings, "json", title="T", meta={"input": "x.csv"}))
        self.assertEqual(data["summary"]["error"], 1)
        self.assertEqual(data["findings"][0]["severity"], "error")
        self.assertEqual(data["meta"]["input"], "x.csv")

    def test_markdown_escapes_pipe(self):
        md = report.render(self.findings, "markdown")
        self.assertIn("a\\|b", md)
        self.assertIn("error 1 / warning 1", md)

    def test_csv_and_html(self):
        csv_text = report.render(self.findings, "csv")
        self.assertTrue(csv_text.startswith("severity,rule_id,location"))
        html_text = report.render([Finding("X", Severity.INFO, "<script>")], "html")
        self.assertIn("&lt;script&gt;", html_text)

    def test_empty(self):
        self.assertIn("指摘はありません", report.render([], "markdown"))


class LogTest(unittest.TestCase):
    def test_redact(self):
        self.assertEqual(log.redact("token=abcdef123456"), "token=***")
        self.assertIn("***", log.redact("Authorization: Bearer abcdefghijklmnopqrstuvwxyz"))
        self.assertEqual(log.describe_text("秘密の本文"), "<text 5 chars>")


class TempTest(unittest.TestCase):
    def test_removed_even_on_error(self):
        holder = {}
        with self.assertRaises(RuntimeError), tempfiles.secure_tempdir() as d:
            holder["d"] = d
            f = d / "readonly.pdf"
            f.write_bytes(b"x")
            os.chmod(f, 0o400)
            raise RuntimeError("boom")
        self.assertFalse(holder["d"].exists())


class ConfigTest(unittest.TestCase):
    def test_dotenv_and_require(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text('# c\nexport DT_TEST_A="1"\nDT_TEST_B=<your-token>\n', encoding="utf-8")
            os.environ.pop("DT_TEST_A", None)
            os.environ.pop("DT_TEST_B", None)
            config.load_dotenv(p)
            self.assertEqual(config.require_env("DT_TEST_A"), "1")
            with self.assertRaises(cli.UsageError):
                config.require_env("DT_TEST_B")

    def test_load_structured(self):
        with tempfile.TemporaryDirectory() as d:
            y = Path(d) / "a.yaml"
            y.write_text("a: 1\n", encoding="utf-8")
            self.assertEqual(config.load_structured(y), {"a": 1})
            with self.assertRaises(cli.UsageError):
                config.load_structured(Path(d) / "missing.yaml")


class FakeResp:
    def __init__(self, status=200, body=b"{}"):
        self.status, self.headers, self._body = status, {}, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class HttpTest(unittest.TestCase):
    def test_retries_on_429_then_succeeds(self):
        calls, sleeps = [], []

        def opener(req, timeout):
            calls.append(req.full_url)
            if len(calls) < 3:
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {"Retry-After": "2"}, None)
            return FakeResp(body=b'{"ok": true}')

        c = HttpClient("https://api.example.com/v1", opener=opener, sleep=sleeps.append, rate_per_sec=1000)
        r = c.get("items", params={"q": "a"})
        self.assertEqual(r.json(), {"ok": True})
        self.assertEqual(len(calls), 3)
        self.assertIn(2.0, sleeps)
        self.assertEqual(calls[0], "https://api.example.com/v1/items?q=a")

    def test_no_retry_on_404(self):
        def opener(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

        c = HttpClient("https://api.example.com", opener=opener, sleep=lambda s: None)
        with self.assertRaises(HttpError) as cm:
            c.get("x")
        self.assertEqual(cm.exception.status, 404)

    def test_rate_limiter_sleeps_when_empty(self):
        t = [0.0]
        slept = []
        rl = RateLimiter(2.0, clock=lambda: t[0], sleep=slept.append)
        rl.acquire()
        rl.acquire()
        self.assertAlmostEqual(slept[0], 0.5)


class CliTest(unittest.TestCase):
    def test_run_main_maps_exceptions(self):
        def usage(_):
            raise cli.UsageError("bad")

        def boom(_):
            raise ValueError("x")

        err = io.StringIO()
        import contextlib

        with contextlib.redirect_stderr(err):
            self.assertEqual(cli.run_main(usage), cli.ExitCode.USAGE)
            self.assertEqual(cli.run_main(boom), cli.ExitCode.RUNTIME)
        self.assertEqual(cli.run_main(lambda _: 0), cli.ExitCode.OK)


if __name__ == "__main__":
    unittest.main()
