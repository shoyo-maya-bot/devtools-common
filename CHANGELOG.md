# Changelog

形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/)、版は [SemVer](https://semver.org/lang/ja/) に従います。

## [Unreleased]

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
