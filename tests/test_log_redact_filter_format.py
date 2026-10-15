"""log.RedactFilter が %d・%.1f・dict 引数のログを壊さず、引数の秘密も伏せる.

通信はしない。値はすべて架空。
"""

from __future__ import annotations

import io
import logging
import unittest

from devtools_common import log as dlog


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


if __name__ == "__main__":
    unittest.main()
