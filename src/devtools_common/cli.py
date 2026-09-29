"""CLI の共通規約: 終了コード・共通オプション・例外の扱い.

全ツールで同じ終了コードを返すことで、AI エージェントや CI から結果を機械判定できる。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from enum import IntEnum

from .log import get_logger, setup_logging


class ExitCode(IntEnum):
    OK = 0  # 正常終了・指摘なし
    FINDINGS = 1  # 正常に処理したが error 重大度の指摘がある
    USAGE = 2  # 引数・設定ファイル・入力スキーマの誤り（前提が崩れている）
    RUNTIME = 3  # 想定外の実行時エラー


class UsageError(Exception):
    """利用者が直せる誤り（引数・設定・入力形式）。ExitCode.USAGE で終了する。"""


OUTPUT_FORMATS = ("markdown", "html", "csv", "json")


def add_common_args(parser: argparse.ArgumentParser, *, with_format: bool = True) -> None:
    """全ツール共通のオプションを追加する."""
    if with_format:
        parser.add_argument(
            "-f",
            "--format",
            choices=OUTPUT_FORMATS,
            default="markdown",
            help="レポートの出力形式（既定: markdown。AI エージェント連携には json）",
        )
    parser.add_argument("-o", "--output", default="-", help="レポートの出力先ファイル（既定: 標準出力）")
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="ログレベル（ログは標準エラーへ。本文は出力しない）",
    )


def run_main(main: Callable[[list[str] | None], int], argv: list[str] | None = None) -> int:
    """main を実行し、例外を終了コードへ変換する."""
    for stream in (sys.stdout, sys.stderr):  # Windows コンソール対策
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    log = get_logger("devtools")
    try:
        return int(main(argv))
    except UsageError as e:
        print(f"error: {e}", file=sys.stderr)
        return ExitCode.USAGE
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001 — 最上位で握って終了コードに変換する
        log.debug("unhandled exception", exc_info=True)
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return ExitCode.RUNTIME


def write_output(text: str, output: str) -> None:
    """レポートを標準出力（"-"）またはファイルへ書く（UTF-8・LF）."""
    if output == "-":
        sys.stdout.write(text)
        sys.stdout.flush()
    else:
        from pathlib import Path

        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def configure_from_args(args: argparse.Namespace) -> None:
    setup_logging(getattr(args, "log_level", "WARNING"))
