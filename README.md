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
| `devtools_common.report` | `Finding`（指摘）と Markdown / HTML / CSV / JSON レポート出力。JSON は `schema_version` 付きで、`report_schema()` が JSON Schema を返す。CSV は数式として実行されないよう `=` `+` `-` `@` タブ・CR で始まるセルの先頭に `'` を付ける（各ツールが CSV・Excel を書くときは `neutralize_formula(value)` を使う） |
| `devtools_common.executables` | 外部ソフト（LibreOffice / Tesseract / draw.io）の探索: 環境変数 `DEVTOOLS_<名前>` → PATH → OS ごとの標準インストール先（Windows の Program Files 等） |
| `devtools_common.llm` | LLM（Anthropic Messages API 互換）の小さなクライアント（標準ライブラリのみ）：再試行・プロンプトキャッシュ・構造化出力・費用の見積もり・伏せ字・テスト用の `FakeLLM` |
| `devtools_common.review_results` | AI レビュー結果（`reviews/*_result.md`：Front Matter + Markdown 表）と判定の履歴（`history.jsonl`）の読み込み。office-review と quality-analytics が同じ規則で読むための部品（下の「レビュー結果の読み込み」） |
| `devtools_common.excel` | Excel の読み取りの共通部品：表示形式どおりの値（`render_value`、和暦・ゼロ埋め・桁区切り等）、結合セルの索引（`MergeMap`）、複数段の見出しの判定（`header_depth` / `header_names`）、計算結果の無い数式セルを数式の文字列で補う読み込み（`load_workbook_with_formula_fallback`）。openpyxl が必要（extra `excel`） |

### HTTP のリトライと二重送信

`HttpClient` は 429 / 5xx / ネットワークエラーを再試行します。ただし二重登録を防ぐため、再試行するのは次のときだけです。

- GET / HEAD / PUT / DELETE / OPTIONS は、上のエラーでいつでも再試行する。
- POST / PATCH は、サーバーに届いていないことが確実なとき（接続拒否・名前解決の失敗）と、
  `Retry-After` 付きの 429 のときだけ再試行する。5xx や読み取りタイムアウトでは再試行しない。
- 何度送っても結果が同じ POST は `idempotent=True` を渡すか、`Idempotency-Key` ヘッダーを付ける。いつでも再試行する。
- 逆に `idempotent=False` を渡すと、GET でも届いた可能性があるときは再試行しない。

```python
client.post("chat.postMessage", json_body=msg)  # 5xx でも再送しない（二重投稿を防ぐ）
client.post("search", json_body=q, idempotent=True)  # 副作用のない POST は再試行してよい
client.post("issues", json_body=issue, headers={"Idempotency-Key": key})  # API が対応していれば再試行してよい
```

### HTTP の送り先の制限

`path` に絶対 URL を渡しても、`base_url` と別のオリジン（スキーム・ホスト・ポート）には送りません（`CrossOriginError`）。
`headers` に入れた認証情報が別のホストへ漏れるのを防ぐためです。`base_url` と同じホストの絶対 URL（ページングの `next` リンク等）は
これまでどおり送れます。別のオリジンへ送るときは `allow_other_origin=True` を明示します。このとき `headers`（既定のヘッダー）は付けず、
呼び出しごとの `headers` だけを付けます。リダイレクトで別のオリジンへ移るときも、既定のヘッダーと認証ヘッダー
（`Authorization` / `Cookie` / `X-Api-Key` 等）を外します。

```python
client = HttpClient("https://api.example.com/v1/", headers={"Authorization": f"Bearer {token}"})
client.get("https://api.example.com/v1/items?page=2")  # 同じホストなので認証付きで送る
client.get("https://files.example.net/a.png", allow_other_origin=True)  # 認証ヘッダーなしで送る
```

## LLM クライアント（`devtools_common.llm`）

Anthropic Messages API と互換のエンドポイントを呼ぶ小さなクライアントです。外部ライブラリ（anthropic SDK）は使いません。

| 名前 | 内容 |
|---|---|
| `LLMClient(model, *, max_tokens, temperature, system, cache_system, masker, ...)` | `complete()` で 1 回呼ぶ。結果は `Completion`（`text` / `content` / `stop_reason` / `usage` / `request_id` / `duration_ms` / `cost_usd`） |
| `LLMClient.complete_json(messages, schema=...)` | 構造化出力。スキーマを `input_schema` にしたツール 1 つを強制し、結果を検証して返す |
| `cached_text(text, ttl=None)` | `cache_control`（ephemeral）付きの text ブロック。何度も使う長い資料に付ける |
| `Usage` / `estimate_cost(model, in, out)` / `PRICES` | トークン数と費用の見積もり（USD）。料金表はモデル ID の接頭辞で引く |
| `Masker(patterns=..., words=...)` | 送る前の伏せ字。メールアドレス・電話番号（全角・区切りなし・+81 を含む）は常に伏せる |
| `FakeLLM(responses)` / `FakeLLM.from_jsonl(path)` | 記録した応答を順に返すテスト用クライアント（通信しない） |
| 例外 | `LLMError` を基底に `APIStatusError` / `APIConnectionError` / `MaxTokensError` / `SchemaValidationError` |

```python
from devtools_common.llm import LLMClient, Masker

client = LLMClient(
    "claude-haiku-4-5",
    max_tokens=1024,
    system=long_guideline,  # 何度も使う長い指示
    cache_system=True,  # cache_control を付けて 2 回目以降を安くする
    masker=Masker(words=["社名"], patterns=[r"EMP-\d{4}"]),
)
answer = client.complete("次の指摘を要約してください: ...").text

schema = {
    "type": "object",
    "properties": {"severity": {"enum": ["MUST", "SHOULD", "MAY"]}, "summary": {"type": "string"}},
    "required": ["severity", "summary"],
}
label = client.complete_json("次の指摘を分類してください: ...", schema=schema)
```

- 接続先は引数 `base_url` → 環境変数 `ANTHROPIC_BASE_URL` → `https://api.anthropic.com` の順。
  API キーは `ANTHROPIC_API_KEY`（ゲートウェイ向けに `ANTHROPIC_AUTH_TOKEN` も使える）
- 429 / 5xx（529 を含む）/ 接続エラー・タイムアウトは再試行します（既定 3 回）。`retry-after` があればそれに従い、
  無ければ指数バックオフ。どちらも `backoff_max` 秒までで、ゆらぎを足します。
  **再送は結果を壊しませんが、サーバーに届いて処理された後に接続が切れた場合は二重に課金されます**。
  費用を優先するときは `max_retries=0` にします
- 出力が `max_tokens` で切れたら `MaxTokensError`（`e.completion` に途中までの結果）
- `complete_json` の検証は、`jsonschema` が入っていればそれを使い、無ければ type・required・enum・properties・items だけを確かめます
- 呼び出しごとに INFO で 1 行のログ（モデル・入出力とキャッシュのトークン数・所要時間・request-id・費用）を出します。
  本文は出しません。`record.llm` に同じ内容の dict が入ります
- 料金表（`PRICES`, USD / 100 万トークン）は llm.py の 1 か所にあります。確認日をコメントに書いています。
  表に無いモデルは費用を `None` にし、「unknown model」の警告を出します（0 円と誤解させないため）
- `record_path=` を渡すと応答を JSONL に記録し、テストでは `FakeLLM.from_jsonl()` で同じ応答を再生できます。
  **応答の本文がファイルに残ります**。業務の文書を扱うときは置き場所に気をつけ、コミットしないでください。
  `record_content=` で本文の扱いを選べます（既定 `"auto"`: `masker` があれば伏せて書く、無ければそのまま。
  `"masked"`: `masker` で伏せる、`"omit"`: 本文を書かずトークン数などだけ残す、`"raw"`: そのまま）。
  `{"status": 529, "headers": {...}, "body": {...}}` の行でエラーと再試行も再現できます

**レポートの形式は全ツール共通の約束です**: [docs/REPORT_FORMAT.md](docs/REPORT_FORMAT.md)（JSON Schema:
`src/devtools_common/schemas/report.schema.json`）。互換性のない変更は schema_version のメジャーを上げます。

## Excel の読み取り（`devtools_common.excel`）

doc-to-markdown（Excel → Markdown）と design-diff（Excel 設計書の差分）が同じ規則でセルを読むための部品です。
openpyxl は extra で入れます（`render_value` と見出しの判定は標準ライブラリだけで動きます）。

```toml
dependencies = [
  "devtools-common[excel] @ git+https://github.com/shoyo-maya-bot/devtools-common.git@<コミット SHA>",
]
```

```python
from devtools_common.excel import MergeMap, header_depth, header_names, load_workbook_with_formula_fallback, render_cell

wb, missing = load_workbook_with_formula_fallback("設計書.xlsx")  # missing: 計算結果が無く数式の文字列を入れたセル
ws = wb.active
merges = MergeMap.from_worksheet(ws)


def own(r, c):  # 結合の左上以外は空
    return "" if merges.is_covered(r, c) else render_cell(ws.cell(r, c))[0]


def filled(r, c):  # 結合の左上の値で埋める
    return render_cell(ws.cell(*merges.anchor(r, c)))[0]


depth = header_depth(merges, own, 1, first_col=1, last_col=ws.max_column, last_row=ws.max_row)
names = header_names(filled, 1, depth, range(1, ws.max_column + 1))  # 例: ["項目名", "型情報/型", "型情報/桁"]
```

| 関数 | 決まりごと |
|---|---|
| `render_value(値, 表示形式)` | `(文字列, 再現できたか)`。`000` → `001`、`#,##0` → `1,500`、`0.0%`、`"¥"#,##0`、`yyyy/mm/dd`、`ggge"年"`（和暦）等。指数・分数・条件付き等は書式なしの値と `False` |
| `header_depth` | 見出し行から始まる結合が下の行まで続き、その行が文字だけなら次の行も見出し。横に結合した見出しの下の各列がすべて文字なら次の行も見出し。最大 3 段。`header_rows=` で段数を指定 |
| `header_names` | 上の段から `/` でつなぐ（`金額/税抜`）。縦結合で同じ値が続く段は 1 つ |
| `load_workbook_with_formula_fallback` | 計算結果（data_only）で読む。計算結果の無い数式セル（openpyxl で作ったブック等）には数式の文字列を入れ、そのセルの一覧を返す |

## レビュー結果の読み込み（`devtools_common.review_results`）

office-review（`harness`）が書き、office-review と quality-analytics が読む「レビュー結果ファイル」と「判定の履歴」を読む部品です。
office-review の `review_harness/results.py`・`history.py` の実装をそのまま移しました（`tests/test_review_results.py` が、
移す前の office-review の実装で読んだ結果と同じになることを確かめています）。依存は PyYAML だけです。

```python
from devtools_common.review_results import (
    ReviewItem,
    ResolvedHistory,
    load_history,
    load_review_dir,
    parse_review_file,
    resolve_history,
)

items, problems = parse_review_file("reviews/2026-10-02_order-api_result.md", doc_types=["api", "db"])
items = load_review_dir("reviews")  # *_result.md をファイル名順に。形式の誤りの行は飛ばす
# 壊れた行は「警告: history.jsonl:<行番号> …」を warn に渡して飛ばす
records = load_history("data/history.jsonl", warn=print)
resolved = resolve_history(records, items)
decision = resolved.decisions.get(items[0].fingerprint, {}).get("decision")  # "adopted" / "rejected" / None
```

指紋 v2（ロール・観点を含む。office-review の harness が `fingerprint_v2` として書き、結び付けに使う値）で引くとき:

```python
from devtools_common.review_results import fingerprint_v2, resolve_history_v2

resolved = resolve_history_v2(records, items)  # decisions のキーは ReviewItem.fingerprint_v2
decision = resolved.decisions.get(items[0].fingerprint_v2, {}).get("decision")
fp = fingerprint_v2("inputs/api/order-api.md", "3. 入力", "必須項目が決まっていない", "tester", "API-003")
```

| 名前 | シグネチャ・戻り値 | 決まりごと |
|---|---|---|
| `parse_review_file` | `(path: str \| Path, *, doc_types: Collection[str] \| None = None) -> tuple[list[ReviewItem], list[Finding]]` | UTF-8（BOM 可）。Front Matter の `target`・`type`・`reviewed_at` が無い、`type` が `doc_types` に無い（渡したときだけ）、表が無い、列数違い・No が数字でない行は `Finding`（`lint.meta` / `lint.table`、error）にして、その行は飛ばす |
| `load_review_dir` | `(reviews_dir: str \| Path, pattern: str = "*_result.md") -> list[ReviewItem]` | ファイル名順に `parse_review_file` した指摘をつなげる（形式の誤りは返さない） |
| `load_history` | `(path: str \| Path, warn: Callable[[str], Any] \| None = None) -> list[dict[str, Any]]` | 1 行 1 判定、書いた順。ファイルが無ければ `[]`。BOM 可。JSON でない・オブジェクトでない・`decision`（adopted / rejected）か `check_id` が無い・`decision` / `check_id` / `key` / `fingerprint` / `fingerprint_v2` が文字列でない・`fingerprint_v2` も `fingerprint` も `key` も無い（空）行は、行番号付きで警告して飛ばす（`warn` の既定は標準エラー） |
| `resolve_history` | `(records: Iterable[dict[str, Any]], items: Iterable[ReviewItem]) -> ResolvedHistory` | 同じ指紋は後の行が勝つ。`fingerprint` の無い旧形式の行は、`key`（ファイル名#No）の指摘の本文と `check_id` が一致するときだけ結び付け、違えば `orphans` |
| `resolve_history_v2` | `(records: Iterable[dict[str, Any]], items: Iterable[ReviewItem]) -> ResolvedHistory` | 指紋 v2 で結び付ける（office-review の `history.resolve` と同じ規則）。`fingerprint_v2` がある行はその値。`fingerprint`（旧指紋）だけの行は、旧指紋が同じいまの指摘の v2 に引き継ぐ（複数あれば行の `role`・`check_id` が一致して 1 つに決まるときだけ。決まらなければ旧指紋のまま残す）。どちらも無い行は `resolve_history` と同じ（`key` で一致するときだけ、違えば `orphans`）。`decisions` のキーと各判定の `fingerprint` は結び付けた指紋 |
| `ResolvedHistory` | `decisions: dict[str, dict]`（指紋 → 最新の判定）、`orphans: list[dict]`、`all_records() -> dict[str, dict]` | `all_records()` は集計用（孤立した旧形式は `legacy:<key>`） |
| `ReviewItem` | dataclass: `file, target, doc_type, reviewed_at: str, no: int, check_id, severity, role, location, finding, basis, invalid_check: str`、プロパティ `key: str`（`ファイル名#No`）・`fingerprint: str`（`fp:` + 16 桁、v1）・`fingerprint_v1: str`（常に v1。サブクラスが `fingerprint` を替えても変わらない）・`fingerprint_v2: str`（v2） | `reviewed_at` は日付なら ISO 形式の文字列 |
| `fingerprint` | `(target: str, location: str, finding: str) -> str` | 指摘の指紋。`target` が空ならファイル名を渡す。`normalize` した値の SHA-256 の先頭 16 桁 |
| `fingerprint_v2` | `(target: str, location: str, finding: str, role: str, check_id: str) -> str` | 指紋 v2。`fingerprint` の 3 つにロールと観点を加える（office-review の `review_harness.results.fingerprint` と同じ値） |
| `normalize` | `(text: str) -> str` | NFKC、空白の連続を 1 つに、前後の空白を除く、英字を小文字に |
| `front_matter` | `(text: str) -> dict[str, Any]` | 先頭の `---` 〜 `---` の YAML。無い・読めない・mapping でなければ `{}`（BOM は読むときに外す） |
| `split_row` | `(line: str) -> list[str]` | 表の 1 行をセルに分ける（エスケープはそのまま。下の「表のエスケープ」） |
| `table_cells` | `(line: str) -> list[str]` | `split_row` の各セルの前後の空白を除き、エスケープした縦棒だけを戻す（下の「表のエスケープ」） |
| `unescape_cell` | `(cell: str) -> str` | セルのエスケープ（二重のバックスラッシュ、エスケープした縦棒）を戻す。`devtools_common.report` の Markdown 出力の逆。`table_cells` は指紋の互換のためエスケープした縦棒だけを戻す |
| 定数 | `COLUMNS`（表の列名 8 つ）、`REQUIRED_META`、`HISTORY_DECISIONS`、`HISTORY_REQUIRED_KEYS` | |

表のエスケープ（office-review の実装と同じ）:

```text
| a\| b |     → セル 1 つ: "a| b"           （\| はセルの中の縦棒）
| a\\| b |    → セル 2 つ: "a\\", "b"       （\\ の後ろの | は区切り。\\ はそのまま残す）
| a | b\|     → セル 2 つ: "a", "b|"         （末尾の \| は区切りではない）
| x<br>y |    → "x<br>y"                    （<br> などは戻さない。指紋が変わらないように）
```

形式を変えるときは、office-review と quality-analytics の互換テストも合わせて直します。

## 取り込み方（ツール側の `pyproject.toml`）

```toml
dependencies = [
  "devtools-common @ git+https://github.com/shoyo-maya-bot/devtools-common.git@<コミット SHA>",
]
```

タグは付け替えられるため、版はコミット SHA で固定します。更新するときは新しい SHA に書き換える PR を出します。

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
pip install -e ".[dev]" -c constraints.txt   # CI と同じ版で入れる（constraints.txt は deps-lock.yml が Python 3.10 で作る）
pytest            # または python -m unittest discover -s tests
python scripts/verify_gates.py   # CI と同じゲートを同じ順で（push 前に）
```

## セキュリティ・ライセンス

- 脆弱性を見つけたら、公開の Issue ではなく [SECURITY.md](SECURITY.md) の方法で知らせてください
- ライセンスは [MIT](LICENSE) です
