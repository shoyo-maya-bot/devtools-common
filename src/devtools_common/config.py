"""設定ファイル・環境変数の読み込み.

秘密情報は環境変数（または .env。コミットしない）から読む。リポジトリには .env.example だけを置く。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import yaml

from .cli import UsageError

_INLINE_COMMENT = re.compile(r"\s#")
# 引用符で囲んだ値（閉じ引用符の後ろは空白と「# コメント」だけ）。閉じ引用符が無ければ囲んでいないものとして読む
_QUOTED = re.compile(r"""^(["'])(.*?)\1\s*(?:#.*)?$""")


def load_structured(path: str | Path) -> Any:
    """拡張子で YAML / JSON を判別して読み込む."""
    p = Path(path)
    if not p.is_file():
        raise UsageError(f"file not found: {p}")
    text = p.read_text(encoding="utf-8-sig")
    try:
        if p.suffix.lower() == ".json":
            return json.loads(text)
        return yaml.safe_load(text)
    except json.JSONDecodeError as e:
        raise UsageError(f"cannot parse {p.name}: {e.msg} (line {e.lineno}, column {e.colno})") from None
    except yaml.YAMLError as e:
        raise UsageError(f"cannot parse {p.name}: {_yaml_error_summary(e)}") from None


def _yaml_error_summary(e: yaml.YAMLError) -> str:
    """YAML の構文エラーを、該当行の中身（api_key などの値が入りうる）を含めずに要約する."""
    if isinstance(e, yaml.MarkedYAMLError):
        parts = [x for x in (e.context, e.problem) if x]
        mark = e.problem_mark or e.context_mark
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark is not None else ""
        return ("; ".join(parts) or type(e).__name__) + where
    return type(e).__name__


def load_dotenv(path: str | Path = ".env", *, override: bool = False) -> dict[str, str]:
    """KEY=VALUE 形式の .env を読み、os.environ に設定する（外部依存なし）.

    - 文字コードは UTF-8（BOM 付きでもよい。メモ帳で保存した .env の先頭のキーが壊れないように）
    - `#` で始まる行と空行は飛ばす。`export KEY=VALUE` も読む
    - 値を `"…"` か `'…'` で囲むと、その中身が値（中の `#` はそのまま）。閉じ引用符の後ろには「# コメント」だけ書ける
    - 囲まない値は、空白の後ろの `#` から行末までをコメントとして除く（`a#b` の `#` は値の一部）
    """
    p = Path(path)
    loaded: dict[str, str] = {}
    if not p.is_file():
        return loaded
    for raw in p.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = _dotenv_value(value)
        loaded[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return loaded


def _dotenv_value(value: str) -> str:
    """.env の値（= の後ろ）: 引用符で囲んだ値は中身、囲まない値は行末の「 # コメント」を除いたもの."""
    stripped = value.strip()
    if value[:1].isspace() and stripped.startswith("#"):  # KEY=  # コメント（値なし）
        return ""
    m = _QUOTED.match(stripped)
    if m:
        return m.group(2)
    m = _INLINE_COMMENT.search(stripped)
    return stripped[: m.start()].rstrip() if m else stripped


def require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value or value.startswith("<your-"):
        raise UsageError(f"environment variable {name} is not set (see .env.example)")
    return value
