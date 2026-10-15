"""RedactFilter: 例外のトレースバック（exc_info）と stack_info も伏せ字にする.

キーの形の文字列は実行時に組み立てる（架空）。
"""

from __future__ import annotations

import io
import logging

from devtools_common.log import RedactFilter

KEY = "sk-" + "ant-api03-" + "Q" * 30


def _logger(stream: io.StringIO) -> logging.Logger:
    logger = logging.getLogger("test.redact.exc_info")
    logger.handlers.clear()
    logger.propagate = False
    h = logging.StreamHandler(stream)
    h.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    h.addFilter(RedactFilter())
    logger.addHandler(h)
    logger.setLevel(logging.DEBUG)
    return logger


def test_exception_traceback_is_redacted():
    out = io.StringIO()
    log = _logger(out)
    try:
        raise RuntimeError(f"bad request with key {KEY}")
    except RuntimeError:
        log.exception("call failed")
    text = out.getvalue()
    assert "Traceback" in text and "RuntimeError" in text
    assert KEY not in text
    assert "***" in text


def test_exc_info_true_on_debug_and_stack_info_are_redacted():
    out = io.StringIO()
    log = _logger(out)
    try:
        raise ValueError(f"api_key: {KEY}")
    except ValueError:
        log.debug("unhandled", exc_info=True, stack_info=True)
    assert KEY not in out.getvalue()


def test_record_keeps_exc_info_for_other_handlers():
    rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "m", (), None)
    try:
        raise KeyError(KEY)
    except KeyError:
        import sys

        rec.exc_info = sys.exc_info()
    RedactFilter().filter(rec)
    assert rec.exc_info is not None and rec.exc_info[0] is KeyError
    assert rec.exc_text and KEY not in rec.exc_text
