"""外部コマンド（LibreOffice / draw.io / Tesseract など）の探索.

探索順:
1. 環境変数（例: DEVTOOLS_SOFFICE=C:\\Tools\\LibreOffice\\program\\soffice.exe）
2. PATH（shutil.which）
3. OS ごとの標準インストール先（Windows の Program Files、macOS の /Applications など）

Windows では LibreOffice・draw.io・Tesseract の既定インストール先が PATH に入らないため、3 が必要になる。
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExecutableSpec:
    key: str  # 表示名・環境変数名の元（例: "soffice" → DEVTOOLS_SOFFICE）
    names: tuple[str, ...]  # PATH で探すコマンド名
    windows: tuple[str, ...] = ()  # %ProgramFiles% 等からの相対パス（"/" 区切りで書く）
    macos: tuple[str, ...] = ()  # 絶対パス
    linux: tuple[str, ...] = ()  # 絶対パス
    install_hint: str = ""

    @property
    def env_var(self) -> str:
        return f"DEVTOOLS_{self.key.upper()}"


SOFFICE = ExecutableSpec(
    key="soffice",
    names=("soffice", "libreoffice"),
    windows=("LibreOffice/program/soffice.exe",),
    macos=("/Applications/LibreOffice.app/Contents/MacOS/soffice",),
    linux=("/opt/libreoffice/program/soffice", "/usr/lib/libreoffice/program/soffice"),
    install_hint="LibreOffice（無料）をインストールしてください: https://www.libreoffice.org/download/",
)

DRAWIO = ExecutableSpec(
    key="drawio",
    names=("drawio", "draw.io"),
    windows=("draw.io/draw.io.exe",),
    macos=("/Applications/draw.io.app/Contents/MacOS/draw.io",),
    linux=("/opt/drawio/drawio",),
    install_hint="draw.io Desktop（無料）をインストールしてください: https://github.com/jgraph/drawio-desktop/releases",
)

TESSERACT = ExecutableSpec(
    key="tesseract",
    names=("tesseract",),
    windows=("Tesseract-OCR/tesseract.exe",),
    macos=("/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"),
    install_hint=(
        "Tesseract OCR（無料）と日本語データ(jpn)をインストールしてください。"
        "Windows: https://github.com/UB-Mannheim/tesseract/wiki（インストール時に Japanese を選択）"
    ),
)


def _windows_roots() -> list[Path]:
    roots = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "LOCALAPPDATA"):
        v = os.environ.get(var)
        if v:
            roots.append(Path(v))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "Programs")  # ユーザー単位インストール（draw.io 等）
    return roots


def candidates(spec: ExecutableSpec, platform: str | None = None) -> Iterable[Path]:
    platform = platform or sys.platform
    if platform == "win32":
        for root in _windows_roots():
            for rel in spec.windows:
                yield root.joinpath(*rel.split("/"))
    elif platform == "darwin":
        yield from (Path(p) for p in spec.macos)
    else:
        yield from (Path(p) for p in spec.linux)


def find_executable(spec: ExecutableSpec) -> str | None:
    """見つかった実行ファイルの絶対パス、無ければ None."""
    override = os.environ.get(spec.env_var, "").strip().strip('"')
    if override:
        p = Path(override)
        return str(p) if p.is_file() else None  # 明示指定が誤っていれば他を探さない（取り違え防止）
    for name in spec.names:
        found = shutil.which(name)
        if found:
            return found
    for cand in candidates(spec):
        if cand.is_file():
            return str(cand)
    return None


def not_found_message(spec: ExecutableSpec) -> str:
    override = os.environ.get(spec.env_var, "").strip()
    if override:
        return f"{spec.env_var} に指定されたファイルがありません: {override}"
    return f"{spec.key} が見つかりません。{spec.install_hint}（場所を指定する場合は環境変数 {spec.env_var}）"
