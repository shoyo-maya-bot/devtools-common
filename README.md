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
| `devtools_common.report` | `Finding`（指摘）と Markdown / HTML / CSV / JSON レポート出力。JSON は `schema_version` 付きで、`report_schema()` が JSON Schema を返す |
| `devtools_common.executables` | 外部ソフト（LibreOffice / Tesseract / draw.io）の探索: 環境変数 `DEVTOOLS_<名前>` → PATH → OS ごとの標準インストール先（Windows の Program Files 等） |

**レポートの形式は全ツール共通の約束です**: [docs/REPORT_FORMAT.md](docs/REPORT_FORMAT.md)（JSON Schema:
`src/devtools_common/schemas/report.schema.json`）。互換性のない変更は schema_version のメジャーを上げます。

## 取り込み方（ツール側の `pyproject.toml`）

```toml
dependencies = [
  "devtools-common @ git+https://github.com/<your-org>/devtools-common.git@v0.2.0",
]
```

社内パッケージレジストリ（例: GitHub Packages / Artifactory / CodeArtifact）で公開する場合は
`devtools-common>=0.2,<0.3` のように範囲指定に置き換えます。

ツール側の `setup.cmd` / `setup.sh`（`scripts/install_deps.py`）は、隣のフォルダに devtools-common があれば
Git 依存の代わりにそれを使います（GitHub の認証なしでセットアップできる）。

## バージョニング

- [SemVer](https://semver.org/lang/ja/)。`pyproject.toml` の `version` が正。
- 公開 API（上表のモジュールの公開関数・クラス）を壊す変更は MAJOR（0.x の間は MINOR）を上げる。
- リリース: `version` と `CHANGELOG.md` を更新 → マージ → `git tag v0.1.0 && git push origin v0.1.0`
  （`release.yml` がタグと `version` の一致を検査し、wheel を GitHub Release に添付）。
- 各ツールは Renovate / Dependabot で依存タグの更新 PR を受け取る運用を推奨。

## 開発

```bash
./setup.sh --dev          # Windows: .\scripts\setup.ps1 -Dev
ruff check . && ruff format --check .
pytest            # または python -m unittest discover -s tests
```
