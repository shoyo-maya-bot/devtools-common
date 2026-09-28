"""設定ファイル・環境変数の読み込み.

秘密情報は環境変数（または .env。コミットしない）から読む。リポジトリには .env.example だけを置く。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

from .cli import UsageError


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
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise UsageError(f"cannot parse {p.name}: {e}") from e


def load_dotenv(path: str | Path = ".env", *, override: bool = False) -> dict[str, str]:
    """KEY=VALUE 形式の .env を読み、os.environ に設定する（外部依存なし）."""
    p = Path(path)
    loaded: dict[str, str] = {}
    if not p.is_file():
        return loaded
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        loaded[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return loaded


def require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value or value.startswith("<your-"):
        raise UsageError(f"environment variable {name} is not set (see .env.example)")
    return value
