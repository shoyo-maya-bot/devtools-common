"""ロギング規約.

- ログは標準エラーへ出す（標準出力はレポート専用）。
- ドキュメント本文・データの値はログに出さない。件数・パス・所要時間などのメタ情報だけを出す。
- 万一メッセージに秘密情報らしき文字列が混ざっても `RedactFilter` が伏せ字にする。
"""

from __future__ import annotations

import logging
import re
import sys

_SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),  # Anthropic の API キー（sk-ant-api03-… / sk-ant-admin01-…）
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}"),
    # Authorization / Proxy-Authorization: Basic <base64(ユーザー:パスワード)>（短くても伏せる）
    re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?basic\s+)[A-Za-z0-9+/=._\-]+"),
    re.compile(r"(?i)((?:password|passwd|secret|token|api[_-]?key)\s*[:=]\s*)\S+"),
]


def redact(text: str) -> str:
    """秘密情報らしき部分を *** に置き換える."""
    for rx in _SECRET_PATTERNS:
        text = rx.sub(lambda m: (m.group(1) if m.groups() else "") + "***", text)
    return text


def describe_text(text: str) -> str:
    """本文の代わりにログへ出す要約（長さのみ）."""
    return f"<text {len(text)} chars>"


class RedactFilter(logging.Filter):
    """メッセージを引数ごと整形してから伏せ字にする.

    `%d`・`%.1f`・dict 引数の書式を保つため、先に `record.getMessage()` で整形し、
    結果を `msg` に入れて `args` を空にする
    （引数を文字列に変えてから整形すると `%d` が TypeError になり、ログが消える）。
    書式と引数が合わないときは、ログを消さずに書式と引数をそのまま並べて残す。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
        except (TypeError, ValueError, KeyError):
            text = f"{record.msg} (log args did not match the format: {record.args!r})"
        record.msg = redact(text)
        record.args = ()
        return True


def setup_logging(level: str | int = "WARNING") -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactFilter())
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
