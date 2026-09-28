"""機密文書を扱うための一時ディレクトリ.

- 作成者のみアクセス可（0o700）。
- with ブロックを抜けると、例外時も含めて必ず削除する。
- 読み取り専用ファイル（Windows で LibreOffice が残すもの等）も権限を戻して削除する。
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .log import get_logger

log = get_logger(__name__)


def _force_remove(func, path, _exc_info) -> None:
    os.chmod(path, stat.S_IWRITE)
    func(path)


@contextmanager
def secure_tempdir(prefix: str = "devtools-") -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        os.chmod(path, 0o700)
        yield path
    finally:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_force_remove)
        else:  # pragma: no cover
            shutil.rmtree(path, onerror=_force_remove)
        if path.exists():  # pragma: no cover — 削除できなかった場合は利用者に知らせる
            log.warning("temporary directory could not be removed: %s", path)
