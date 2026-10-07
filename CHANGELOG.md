# Changelog

形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/)、版は [SemVer](https://semver.org/lang/ja/) に従います。

## [Unreleased]

### Added

- `excel`: Excel の読み取りの共通部品（extra `excel` で openpyxl を入れる）。doc-to-markdown と design-diff に重複していた処理をここへ移した。
  `render_value` / `render_cell`（表示形式どおりの文字列。doc-to-markdown の xlsx_numfmt から移動）、`MergeMap`（結合セルの索引）、
  `header_depth` / `header_names`（複数段の見出しの判定と「親/子」の列名。`header_rows` で段数を指定可）、`looks_numeric`、
  `load_workbook_with_formula_fallback`（計算結果の無い数式セルを数式の文字列で補う。design-diff から移動）
- `scripts/install_deps.py`: 隣の devtools-common を使うとき、`devtools-common[excel]` のような extra も引き継ぐ
- `llm`: Anthropic Messages API 互換エンドポイントの共通クライアント（標準ライブラリのみ）。
  `LLMClient`（`complete` / `complete_json`）、`retry-after` を優先した再試行（ゆらぎ・上限付き）、`cache_control` 付きシステムプロンプト、
  `MaxTokensError`、`Usage` と呼び出しごとのログ、料金表と `estimate_cost`、`Masker`（メールアドレス・電話番号・任意のパターン）、
  テスト用の `FakeLLM`（記録した JSONL の再生）
- `httpclient`: `request()` に `allow_other_origin` 引数、`CrossOriginError`、`safe_urlopen`
- `report.neutralize_formula(value)`: CSV・Excel に書くセルの値を、数式として実行されない形にする（`=` `+` `-` `@` タブ・CR で始まれば先頭に `'`）
- `llm.LLMClient`: `record_content` 引数（`"auto"` / `"masked"` / `"omit"` / `"raw"`）。`record_path` に書く本文を伏せる・省く
- CI: Windows（windows-latest）でもテストを流す。`constraints.txt`（`.github/workflows/deps-lock.yml` が作る）で CI の依存の版を固定

### Changed

- `report`: CSV 出力で、`=` `+` `-` `@` タブ・CR で始まるセルの先頭に `'` を付けるようにした（CSV / 数式インジェクション対策）。
  CSV を機械で読み直している利用者は、先頭の `'` を外す必要がある（JSON 出力は変わらない）
- `llm`: `masker` を渡したクライアントは、`record_path` に書く応答の本文も伏せるようにした（以前は伏せずに書いていた）。
  以前と同じに書くには `record_content="raw"` を渡す

### Fixed

- `log.RedactFilter`: 引数をすべて文字列に変えてから整形していたため、`%d`・`%.1f` のログが TypeError になり、
  「--- Logging error ---」が出てログが消えていた（llm の費用ログ・httpclient の再試行ログ）。先に整形してから伏せ字にする
- `httpclient`: `path` に「?」があるときに `params` を足すと `items?page=2?q=x` になっていた。`&` でつなぐ
- `httpclient`: `Retry-After` が負の値（`-5`）や `nan` のとき、`time.sleep` の ValueError が漏れていた。負の値は 0 にし、`nan` は無視する

### Security

- `httpclient`: `path` に絶対 URL を渡すと、`headers` の認証情報を `base_url` と別のホストへ送っていた。
  別のオリジン（スキーム・ホスト・ポート）へは `CrossOriginError` で送らないようにした。同じホストの絶対 URL はこれまでどおり送れる。
  別のオリジンへ送るときは `allow_other_origin=True` を明示し、既定のヘッダーは付けない
- `httpclient`: 別のオリジンへのリダイレクトでも認証ヘッダーを引き継いでいた。既定のヘッダーと `Authorization` / `Cookie` /
  `X-Api-Key` 等を外してから辿るようにした（既定の `opener` を `safe_urlopen` に変更）

## [0.3.0] - 2026-10-05

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
