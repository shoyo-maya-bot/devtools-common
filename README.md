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
| `devtools_common.llm` | LLM（Anthropic Messages API 互換）の小さなクライアント（標準ライブラリのみ）：再試行・プロンプトキャッシュ・構造化出力・費用の見積もり・伏せ字・テスト用の `FakeLLM` |

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
  `{"status": 529, "headers": {...}, "body": {...}}` の行でエラーと再試行も再現できます

**レポートの形式は全ツール共通の約束です**: [docs/REPORT_FORMAT.md](docs/REPORT_FORMAT.md)（JSON Schema:
`src/devtools_common/schemas/report.schema.json`）。互換性のない変更は schema_version のメジャーを上げます。

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
pytest            # または python -m unittest discover -s tests
python scripts/verify_gates.py   # CI と同じゲートを同じ順で（push 前に）
```

## セキュリティ・ライセンス

- 脆弱性を見つけたら、公開の Issue ではなく [SECURITY.md](SECURITY.md) の方法で知らせてください
- ライセンスは [MIT](LICENSE) です
