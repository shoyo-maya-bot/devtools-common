# レポート出力形式 仕様書（schema_version 1.0）

devtools 系のすべてのツール（`doc2md` / `fieldcheck` / テンプレートから作ったツール）が共通で使う、
**実行結果レポート** の形式です。人が読む形式（Markdown / HTML）と、機械が読む形式（JSON / CSV）があります。

- 機械可読の正: [`src/devtools_common/schemas/report.schema.json`](../src/devtools_common/schemas/report.schema.json)（JSON Schema 2020-12）
- 実装: `devtools_common.report`

## 1. 出力先と選び方

| オプション | 既定 | 説明 |
|---|---|---|
| `-f, --format` | `markdown` | `markdown` / `html` / `csv` / `json` |
| `-o, --output` | `-`（標準出力） | ファイルに書く場合はパス。文字コードは UTF-8（BOM なし）、改行は LF |

- **標準出力にはレポートだけ** を出します。ログ・進捗・警告は標準エラーに出るため、`> report.json` で安全に保存できます。
- AI エージェントや他ツールから使うときは `--format json` を使ってください。Markdown / HTML の見た目は予告なく変わることがあります。

## 2. 終了コード（全ツール共通）

| コード | 意味 | 利用側の扱い |
|---|---|---|
| `0` | 正常終了。指摘なし（または閾値未満の指摘のみ） | 成功 |
| `1` | 正常に処理したが、閾値以上の指摘がある（既定は `error` があるとき） | レポートの `findings` を確認 |
| `2` | 使い方・設定ファイル・入力スキーマの誤り（**前提が崩れている**） | データではなく引数・設定・列名を直す。レポートは出ない場合がある（理由は標準エラー） |
| `3` | 想定外の実行時エラー | 不具合として報告（`--log-level DEBUG` の出力を添える。本文は含まれない） |
| `130` | 中断（Ctrl+C） | — |

## 3. JSON 形式

```json
{
  "schema_version": "1.0",
  "tool": "fieldcheck",
  "tool_version": "0.2.0",
  "title": "fieldcheck 検証レポート",
  "meta": { "input": "expense.csv", "records": 6 },
  "summary": { "error": 1, "warning": 0, "info": 0 },
  "findings": [
    {
      "rule_id": "EXP-003",
      "severity": "error",
      "message": "合計 が 金額 + 税額 と一致しません",
      "location": "row 2",
      "field": "合計",
      "expected": "8800",
      "actual": "8900",
      "suggestion": "合計 または内訳（金額, 税額）を確認してください"
    }
  ]
}
```

### トップレベル

| キー | 型 | 説明 |
|---|---|---|
| `schema_version` | string | この仕様の版（`"メジャー.マイナー"`）。**利用側はメジャーが `1` であることを確認** する |
| `tool` | string | CLI 名（`doc2md` / `fieldcheck` …） |
| `tool_version` | string | ツールの版 |
| `title` | string | レポートの表題（表示用） |
| `meta` | object | 実行情報。キーはツールごと（§5）。値は文字列・数値・真偽値のみ |
| `summary` | object | 重大度ごとの件数 `{error, warning, info}`（整数、必ず 3 キーとも出る） |
| `findings` | array | 指摘の一覧（0 件なら空配列） |

### findings の各要素

すべてのキーが **必ず出力** されます（値が無い場合は空文字 `""`。`null` にはならない）。

| キー | 説明 | 例 |
|---|---|---|
| `rule_id` | 指摘の種類。ツールの仕様（§5）に一覧 | `EXP-003`, `loss.text`, `schema.type` |
| `severity` | `error` / `warning` / `info` | |
| `message` | 人向けの説明（日本語） | |
| `location` | 該当箇所 | `row 2`, `line 5`, `record 3`, `expense.xlsx` |
| `field` | 該当項目（列名など） | `合計` |
| `expected` | 期待値 | `8800` |
| `actual` | 実測値 | `8900` |
| `suggestion` | 修正提案 | |

### 並び順

`findings` は **処理した順**（入力の先頭から）です。同じ入力・同じ設定なら同じ順序・同じ内容になります（決定的）。
ただし `doc2md` の `meta` に含まれるパスは実行時の指定どおりです。

## 4. CSV / Markdown / HTML

- **CSV**: 1 行目がヘッダ `severity,rule_id,location,field,message,expected,actual,suggestion`。1 指摘 = 1 行。
  UTF-8（BOM なし）。Excel で開く場合は「データ → テキストまたは CSV から」で UTF-8 を指定してください。
- **Markdown / HTML**: 人が読むための形式。列は CSV と同じ。`meta` と件数の要約が先頭に付きます。

## 5. ツールごとの meta と rule_id

| ツール | 仕様 |
|---|---|
| doc2md | [doc-to-markdown/docs/OUTPUT_SPEC.md](https://github.com/<your-org>/doc-to-markdown/blob/main/docs/OUTPUT_SPEC.md) |
| fieldcheck | [field-validation/docs/OUTPUT_SPEC.md](https://github.com/<your-org>/field-validation/blob/main/docs/OUTPUT_SPEC.md) |

## 6. 互換性ポリシー

| 変更 | 版の上げ方 | 例 |
|---|---|---|
| キーの追加（`meta` のキー、新しい `rule_id` を含む） | `schema_version` のマイナー | 1.0 → 1.1 |
| キーの削除・改名・型の変更、終了コードの意味の変更 | `schema_version` のメジャー | 1.x → 2.0 |

利用側は **知らないキーや rule_id を無視** するように作ってください（マイナー更新で壊れないため）。
