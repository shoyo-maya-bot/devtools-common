# AGENTS.md — AI エージェント向けガイド（devtools-common）

- このライブラリは全ツールが依存する。**公開 API（`cli` / `log` / `tempfiles` / `config` / `httpclient` / `report` / `executables` の
  公開関数・クラス）を壊さない**。壊す場合は version を上げ（0.x の間は MINOR）、CHANGELOG に移行方法を書く。
- レポートの JSON 形式は docs/REPORT_FORMAT.md と schemas/report.schema.json が正。変えるときは両方と SCHEMA_VERSION を更新する。
- ツール固有の処理をここに入れない。2 つ以上のツールで必要になったものだけを移す。
- 依存は最小（現在は PyYAML のみ）。HTTP は標準ライブラリで実装している。
- 変更後に必ず実行: `ruff check . && ruff format --check . && pytest`
- リリース: `pyproject.toml` の version と CHANGELOG を更新 → マージ → `v<version>` タグを push。
  各ツールは Dependabot の PR でタグを更新する。
