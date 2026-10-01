"""devtools-common — 開発支援ツール群の共有ライブラリ.

各ツールリポジトリはコミット SHA で固定した Git 依存として取り込む（フォルダ共有はしない）:

    devtools-common @ git+https://github.com/shoyo-maya-bot/devtools-common.git@<コミット SHA>
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("devtools-common")
except PackageNotFoundError:  # ソースツリーから直接 import した場合
    __version__ = "0.0.0+local"

__all__ = ["__version__"]
