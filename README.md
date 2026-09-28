# devtools-common

開発支援ツール群（1 ツール = 1 リポジトリ）が共通で使う Python ライブラリです。
各ツールは **Git タグ付き依存** として取り込みます（フォルダ共有・コピーはしない）。

## 提供するもの

| モジュール | 内容 |
|---|---|
| `devtools_common.cli` | 終了コード規約（0 OK / 1 指摘あり / 2 使い方・設定の誤り / 3 実行時エラー）、共通オプション（`--format` `--output` `--log-level`）、`run_main` |
| `devtools_common.log` | 標準エラーへのログ設定、秘密情報の伏せ字フィルタ、本文をログに出さないための `describe_text` |
| `devtools_common.tempfiles` | `secure_tempdir()`：0700・例外時も必ず削除する一時ディレクトリ |
| `devtools_common.config` | YAML/JSON 読み込み、`.env` 読み込み（外部依存なし）、`require_env()` |
| `devtools_common.httpclient` | 外部 API 用クライアント：レート制限・指数バックオフ・`Retry-After` 対応（標準ライブラリのみ） |
| `devtools_common.report` | `Finding`（指摘）と Markdown / HTML / CSV / JSON レポート出力 |

## 取り込み方（ツール側の `pyproject.toml`）

```toml
dependencies = [
  "devtools-common @ git+https://github.com/<your-org>/devtools-common.git@v0.1.0",
]
```

社内パッケージレジストリ（例: GitHub Packages / Artifactory / CodeArtifact）で公開する場合は
`devtools-common>=0.1,<0.2` のように範囲指定に置き換えます。

## バージョニング

- [SemVer](https://semver.org/lang/ja/)。`pyproject.toml` の `version` が正。
- 公開 API（上表のモジュールの公開関数・クラス）を壊す変更は MAJOR（0.x の間は MINOR）を上げる。
- リリース: `version` と `CHANGELOG.md` を更新 → マージ → `git tag v0.1.0 && git push origin v0.1.0`
  （`release.yml` がタグと `version` の一致を検査し、wheel を GitHub Release に添付）。
- 各ツールは Renovate / Dependabot で依存タグの更新 PR を受け取る運用を推奨。

## 開発

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest            # または python -m unittest discover -s tests
```
