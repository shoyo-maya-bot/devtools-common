# Changelog

形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/)、版は [SemVer](https://semver.org/lang/ja/) に従います。

## [Unreleased]

### Added

- `scripts/verify_gates.py`、AGENTS.md に AI エージェントの約束
- `httpclient`: `request()` / `get()` / `post()` に `idempotent` 引数。`Idempotency-Key` ヘッダーがあれば冪等とみなす

### Fixed

- `httpclient`: POST / PATCH を 5xx・タイムアウトで再送していたため、Slack の投稿や課題・コメントが二重に作られることがあった。
  POST / PATCH は、届いていないことが確実なとき（接続拒否・名前解決の失敗）と `Retry-After` 付きの 429 のときだけ再試行する。
  GET / HEAD / PUT / DELETE / OPTIONS はこれまでどおり再試行する。
  副作用のない POST を再試行したいときは `idempotent=True` を渡す

## [0.2.0] - 2026-09-30

### Added

- `executables`: 外部ソフトの探索（環境変数 → PATH → Windows / macOS / Linux の標準インストール先）
- `report`: JSON に `schema_version`（1.0）・`tool`・`tool_version`、`report_schema()`、`schemas/report.schema.json`、docs/REPORT_FORMAT.md
- `cli.write_output()`（出力先ディレクトリの作成・UTF-8・LF）
- setup.cmd / setup.sh

### Fixed

- `tempfiles.secure_tempdir` が Python 3.12 以降で非推奨の引数を使っていた

## [0.1.0] - 2026-09-28

### Added

- 初版
