"""LLM（Anthropic Messages API 互換のエンドポイント）の共通クライアント（標準ライブラリのみ）.

- 接続先: `base_url`（既定は環境変数 ANTHROPIC_BASE_URL、無ければ https://api.anthropic.com）に POST /v1/messages
- 認証: 環境変数 ANTHROPIC_API_KEY（x-api-key）。
  ゲートウェイ向けに ANTHROPIC_AUTH_TOKEN（Authorization: Bearer）も使える
- 再試行: 429 / 5xx（529 を含む）/ 接続エラー・タイムアウトを、`retry-after` を優先した指数バックオフ
  （ゆらぎ付き・上限あり）で再試行する。証明書の検証エラーなどの TLS の誤り・URL の誤りのように
  再送しても直らない失敗は再試行せず、すぐ APIConnectionError にする。
  `http.client.HTTPException`（応答の 1 行目が壊れている BadStatusLine 等）も APIConnectionError に包む。
  **注意**: Messages API の呼び出しはデータを変更しないため再送しても結果は壊れないが、サーバーに届いて処理された後に
  接続が切れた場合は、再送した分も課金される（二重課金のおそれ）。`max_retries=0` で再試行しない
- `stop_reason == "max_tokens"`（出力が途中で切れた）は MaxTokensError
- `complete_json(schema=...)`: ツール 1 つ（input_schema = 渡した JSON Schema）を強制して構造化出力を受け取り、検証する
  （jsonschema があれば使い、無ければ required・enum・type だけの簡易検査）
- 呼び出しごとに 1 行のログ（モデル・トークン数・所要時間・request-id・費用の見積もり）を出す。本文はログに出さない
- `Masker`: 送る前にメールアドレス・電話番号（全角・+81 を含む）と任意のパターンを伏せる。
  区切りの無い数字だけの列は、携帯・IP 電話の形（070/080/090/050 + 8 桁）か、直前に「電話」「TEL」などの語が
  あるときだけ電話番号とみなす（`ID 0123456789` のような注文番号は伏せない）
- `FakeLLM`: 記録した応答（JSONL）を順に返す、テスト用のクライアント（通信しない）
- `record_path`: 応答を JSONL に記録する。本文がファイルに残るので注意。
  `record_content` で伏せる（masker があれば既定で伏せる）・省く（"omit"）を選べる
"""

from __future__ import annotations

import email.message
import http.client
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .httpclient import is_permanent_network_error, retry_delay, safe_urlopen
from .log import describe_text, get_logger

log = get_logger(__name__)

DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

__all__ = [
    "APIConnectionError",
    "APIStatusError",
    "Completion",
    "FakeLLM",
    "LLMClient",
    "LLMError",
    "MASK",
    "Masker",
    "MaxTokensError",
    "PRICES",
    "Price",
    "RECORD_CONTENT_MODES",
    "SECRET_PATTERNS",
    "SchemaValidationError",
    "Usage",
    "cached_text",
    "estimate_cost",
    "price_for",
    "validate",
]


# record_path に書く本文の扱い（LLMClient の record_content）
RECORD_CONTENT_MODES = ("auto", "raw", "masked", "omit")
# content ブロックのうち本文ではないキー（伏せない・省かない）
_RECORD_META_KEYS = frozenset({"type", "id", "name", "tool_use_id", "cache_control", "signature"})


def _mask_deep(value: Any, masker: Masker) -> Any:
    """入れ子の dict / list の中の文字列をすべて伏せる（キーはそのまま）."""
    if isinstance(value, str):
        return masker.text(value)
    if isinstance(value, dict):
        return {k: _mask_deep(v, masker) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_deep(v, masker) for v in value]
    return value


# ---------------------------------------------------------------- 例外


class LLMError(Exception):
    """このモジュールの例外の基底."""


class APIStatusError(LLMError):
    """API がエラーを返した（再試行しても失敗した）."""

    def __init__(self, status: int, error_type: str, message: str, request_id: str | None = None):
        super().__init__(
            f"HTTP {status} {error_type}: {message}" + (f" (request-id {request_id})" if request_id else "")
        )
        self.status, self.error_type, self.request_id = status, error_type, request_id


class APIConnectionError(LLMError):
    """接続できなかった・タイムアウトした（再試行しても失敗した）."""


def _is_permanent_connection_error(e: BaseException) -> bool:
    """再送しても直らない接続の失敗か（再試行しない）. 判定は httpclient.is_permanent_network_error（共通）."""
    return is_permanent_network_error(e)


class MaxTokensError(LLMError):
    """出力が max_tokens で途中で切れた. `completion` に途中までの結果が入っている."""

    def __init__(self, completion: Completion):
        super().__init__(
            f"output was cut off at max_tokens ({completion.usage.output_tokens} tokens); "
            "raise max_tokens or shorten the task"
        )
        self.completion = completion


class SchemaValidationError(LLMError):
    """complete_json の結果がスキーマに合わない（または構造化出力が返らなかった）."""

    def __init__(self, message: str, data: Any = None):
        super().__init__(message)
        self.data = data


# ---------------------------------------------------------------- 料金


@dataclass(frozen=True)
class Price:
    """USD / 100 万トークン. cache_write は 5 分キャッシュの書き込み単価."""

    input: float
    output: float
    cache_write: float
    cache_read: float


# 料金表（USD / 100 万トークン）. キーはモデル ID の接頭辞（日付付きの ID・-latest にも当たる）。
# 確認日: 2026-10-29 https://platform.claude.com/docs/en/about-claude/pricing — 使う前に公式の料金表で確かめる。
# cache_read は多くのモデルで入力の 0.1 倍だが、Opus 5.5・Sonnet 5.5 は 0.05 倍（公式の料金表の注記）。
# ここに無いモデルは費用を None とし、警告を出す。
PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(4.0, 20.0, 5.0, 0.20),
    "claude-sonnet-5-5": Price(2.0, 10.0, 2.50, 0.10),
    "claude-opus-5": Price(5.0, 25.0, 6.25, 0.50),
    "claude-sonnet-5": Price(2.0, 10.0, 2.50, 0.20),
    "claude-opus-4-8": Price(5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-7": Price(5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-6": Price(5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-5": Price(5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-1": Price(15.0, 75.0, 18.75, 1.50),
    "claude-opus-4": Price(15.0, 75.0, 18.75, 1.50),
    "claude-sonnet-4-6": Price(3.0, 15.0, 3.75, 0.30),
    "claude-sonnet-4-5": Price(3.0, 15.0, 3.75, 0.30),
    "claude-sonnet-4": Price(3.0, 15.0, 3.75, 0.30),
    "claude-haiku-4-5": Price(1.0, 5.0, 1.25, 0.10),
    "claude-3-5-haiku": Price(0.80, 4.0, 1.0, 0.08),
}
# 接頭辞の後ろに続いてよいもの（日付・-latest・Vertex の @日付）。
# 「claude-opus-4」が未知の「claude-opus-4-9」に当たらないようにする
_PRICE_SUFFIX = re.compile(r"(?:[-@]\d{8})?(?:-v\d+(?::\d+)?)?(?:-latest)?")
_warned_models: set[str] = set()


def price_for(model: str) -> Price | None:
    """モデル ID の料金. 一番長く一致する接頭辞を使う. 料金表に無ければ None."""
    for prefix in sorted(PRICES, key=len, reverse=True):
        if model.startswith(prefix) and _PRICE_SUFFIX.fullmatch(model[len(prefix) :]):
            return PRICES[prefix]
    return None


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> float | None:
    """費用の見積もり（USD）. 料金表に無いモデルは警告を出して None を返す（0 円と誤解させない）.

    input_tokens はキャッシュを除いた入力トークン数（API の usage.input_tokens と同じ意味）。
    キャッシュの書き込みは 5 分キャッシュの単価で計算する（1 時間キャッシュは実際より安く出る）。
    """
    price = price_for(model)
    if price is None:
        if model not in _warned_models:
            _warned_models.add(model)
            log.warning("unknown model %r: no price in devtools_common.llm.PRICES; cost is not estimated", model)
        return None
    return (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_creation_input_tokens * price.cache_write
        + cache_read_input_tokens * price.cache_read
    ) / 1_000_000


# ---------------------------------------------------------------- 結果


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @classmethod
    def from_api(cls, data: Mapping[str, Any] | None) -> Usage:
        d = data or {}
        return cls(
            *(
                int(d.get(k) or 0)
                for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
            )
        )

    def cost(self, model: str) -> float | None:
        return estimate_cost(
            model,
            self.input_tokens,
            self.output_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens,
        )


@dataclass
class Completion:
    text: str  # text ブロックをつないだもの
    content: list[dict[str, Any]]  # API の content（tool_use ブロック等を含む）
    stop_reason: str | None
    model: str
    usage: Usage
    request_id: str | None = None
    duration_ms: int = 0
    cost_usd: float | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def cached_text(text: str, ttl: str | None = None) -> dict[str, Any]:
    """`cache_control`（ephemeral）付きの text ブロック. 何度も使う長いシステムプロンプト・資料に付ける.

    system や messages の content にそのまま入れられる。ttl は None（既定 5 分）か "1h"。
    キャッシュされるのは一定の長さ（モデルにより 1024〜4096 トークン程度）以上のときだけ。
    """
    cc: dict[str, Any] = {"type": "ephemeral"}
    if ttl:
        cc["ttl"] = ttl
    return {"type": "text", "text": text, "cache_control": cc}


# ---------------------------------------------------------------- 伏せ字

MASK = "＊＊＊"
_D = "[0-9０-９]"
_SEP = r"[\s\-‐‑–—―−－ー.．()（）]"
_ALNUM = "A-Za-z0-9Ａ-Ｚａ-ｚ０-９"
EMAIL_RE = re.compile(rf"[{_ALNUM}._%+\-．＿％＋－]+\s*[@＠]\s*[{_ALNUM}\-－]+(?:\s*[.．]\s*[{_ALNUM}\-－]+)+")
PHONE_RE = re.compile(rf"(?<![0-9０-９])(?:[+＋]\s*[8８][1１]|[0０])(?:{_SEP}{{0,2}}{_D}){{8,11}}(?![0-9０-９])")
# 区切りの無い 11 桁の携帯（070/080/090）・IP 電話（050）・FMC（060）は、周りの語が無くても電話番号とみなす
_MOBILE_RE = re.compile(r"[0０][5-9５-９][0０][0-9０-９]{8}")
# 区切りの無い数字の列の直前（_CONTEXT_CHARS 文字以内）にこの語があれば電話番号とみなす
_CONTEXT_CHARS = 12
_PHONE_CONTEXT_RE = re.compile(
    r"(?:電話|でんわ|携帯|連絡先|問い?合わ?せ先|ＴＥＬ|ＦＡＸ|tel|fax|phone|mobile|☎|℡)", re.IGNORECASE
)
_DOT = ".．"


def _is_decimal(m: re.Match[str]) -> bool:
    """PHONE_RE の一致が小数の一部か. 「0.」で始まる、または直前が「数字 + 小数点」（1.0312345678 の 0 以降）."""
    s, start = m.group(0), m.start()
    if len(s) > 1 and s[1] in _DOT:
        return True
    return start >= 2 and m.string[start - 1] in _DOT and re.fullmatch(_D, m.string[start - 2]) is not None


# 秘密情報らしい文字列（API キー・アクセストークン）. Masker(secrets=True)（既定）で伏せる
_TOKEN_EDGE = r"(?<![A-Za-z0-9_\-])"  # 語の途中（task-… や risk-… など）から始まるものは対象外
SECRET_PATTERNS: tuple[str, ...] = (
    _TOKEN_EDGE + r"sk-ant-[A-Za-z0-9_\-]{16,}",  # Anthropic（sk-ant-api03-… / sk-ant-admin01-…）
    _TOKEN_EDGE + r"sk-[A-Za-z0-9_\-]{20,}",  # OpenAI 形式（sk-… / sk-proj-…）
    r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])",  # AWS アクセスキー ID
    _TOKEN_EDGE + r"gh[pousr]_[A-Za-z0-9]{36,}",  # GitHub トークン（ghp_ / gho_ / ghu_ / ghs_ / ghr_）
    _TOKEN_EDGE + r"github_pat_[A-Za-z0-9_]{22,}",  # GitHub fine-grained トークン
    _TOKEN_EDGE + r"xox[abposr]-[A-Za-z0-9\-]{10,}",  # Slack トークン
)
SECRET_RE = re.compile("|".join(f"(?:{p})" for p in SECRET_PATTERNS))


@dataclass
class Masker:
    """送る前に伏せ字にする. メールアドレス・日本の電話番号（全角・+81 を含む）は常に伏せる.

    区切りの無い数字だけの列は、携帯・IP 電話の形か直前に「電話」「TEL」などの語があるときだけ伏せる（README 参照）。
    secrets=True（既定）なら API キー・トークンらしい文字列（`SECRET_PATTERNS`: sk-ant-…、sk-…、AKIA…、ghp_…、
    github_pat_…、xox?-…）も伏せる。

    words: そのまま伏せる語（社名・システム名など）。patterns: 伏せる正規表現。replacement: 置き換える文字列。
    secrets: False で秘密情報らしい文字列の既定パターンを使わない。
    """

    patterns: Sequence[str] = ()
    words: Sequence[str] = ()
    replacement: str = MASK
    secrets: bool = True

    def __post_init__(self) -> None:
        self._res = [re.compile(p) for p in self.patterns if p]
        self._words = sorted({w for w in self.words if w}, key=len, reverse=True)

    def _phone(self, m: re.Match[str]) -> str:
        s = m.group(0)
        digits = len(re.findall(_D, s))
        if s[0] in "+＋":
            return self.replacement if 11 <= digits <= 12 else s  # 81 + 9〜10 桁
        if not 10 <= digits <= 11:
            return s
        if _is_decimal(m):  # 0.1234567890 のような小数（電話番号は「0.」で始まらない）
            return s
        if digits != len(s):  # 区切り（ハイフン・空白・括弧など）付き
            return self.replacement
        # 区切りの無い数字だけの列: 注文番号・ID と区別できないので、携帯・IP 電話の形か、直前の語で判定する
        before = m.string[max(0, m.start() - _CONTEXT_CHARS) : m.start()]
        if _MOBILE_RE.fullmatch(s) or _PHONE_CONTEXT_RE.search(before):
            return self.replacement
        return s

    def text(self, s: str) -> str:
        if not s:
            return s
        for w in self._words:
            s = s.replace(w, self.replacement)
        if self.secrets:  # メール・電話より先に（キーの中の数字の列を電話番号として一部だけ伏せないように）
            s = SECRET_RE.sub(self.replacement, s)
        s = EMAIL_RE.sub(self.replacement, s)
        s = PHONE_RE.sub(self._phone, s)
        for r in self._res:
            s = r.sub(self.replacement, s)
        return s

    __call__ = text

    def content(self, value: Any) -> Any:
        """文字列、または content ブロックの list の本文を伏せる.

        - text ブロック: `text`
        - tool_use ブロック（assistant がツールに渡した引数）: `input` の中の文字列をすべて（キーはそのまま）
        - tool_result ブロック（ツールの結果）: `content`（文字列、またはブロックの list を同じ規則で）
        - document ブロック: `title`・`context`、`source` が text なら `data`、content なら `content`
          （base64 の PDF などは中身を伏せられないのでそのまま）
        - search_result ブロック: `title`・`content`
        それ以外（画像など）と、`type`・`id`・`name`・`tool_use_id` などの本文でないキーはそのまま。
        """
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self._block(b) for b in value]
        return value

    def _block(self, b: Any) -> Any:
        if not isinstance(b, dict):
            return b
        out = dict(b)
        if isinstance(b.get("text"), str):
            out["text"] = self.text(b["text"])
        kind = b.get("type")
        if kind == "tool_use" and "input" in b:
            out["input"] = _mask_deep(b["input"], self)
        elif kind == "tool_result" and "content" in b:
            out["content"] = self.content(b["content"])
        elif kind in ("document", "search_result"):
            for key in ("title", "context"):
                if isinstance(b.get(key), str):
                    out[key] = self.text(b[key])
            if kind == "search_result" and "content" in b:
                out["content"] = self.content(b["content"])
            src = b.get("source")
            if kind == "document" and isinstance(src, dict):
                src_out = dict(src)
                if src.get("type") == "text" and isinstance(src.get("data"), str):
                    src_out["data"] = self.text(src["data"])
                elif src.get("type") == "content" and "content" in src:
                    src_out["content"] = self.content(src["content"])
                out["source"] = src_out
        return out


# ---------------------------------------------------------------- スキーマ検査

_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


def _minimal_errors(data: Any, schema: Mapping[str, Any], path: str = "$") -> list[str]:
    """jsonschema が無いときの簡易検査（type・required・enum・properties・items だけ）."""
    errs: list[str] = []
    t = schema.get("type")
    if t is not None:
        types = [t] if isinstance(t, str) else list(t)
        ok = any(
            isinstance(data, _TYPES.get(x, (object,))) and not (x in ("integer", "number") and isinstance(data, bool))
            for x in types
        )
        if not ok:
            return [f"{path}: expected {t}, got {type(data).__name__}"]
    if "enum" in schema and data not in schema["enum"]:
        errs.append(f"{path}: {data!r} is not one of {schema['enum']}")
    if isinstance(data, dict):
        errs += [f"{path}: missing required key {k!r}" for k in schema.get("required", []) if k not in data]
        for k, sub in (schema.get("properties") or {}).items():
            if k in data and isinstance(sub, Mapping):
                errs += _minimal_errors(data[k], sub, f"{path}.{k}")
    if isinstance(data, list) and isinstance(schema.get("items"), Mapping):
        for i, v in enumerate(data):
            errs += _minimal_errors(v, schema["items"], f"{path}[{i}]")
    return errs


def _json_path(parts: Iterable[Any]) -> str:
    return "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in parts)


def validate(data: Any, schema: Mapping[str, Any]) -> None:
    """data が schema に合わなければ SchemaValidationError. jsonschema があれば使う."""
    try:
        import jsonschema
    except ImportError:
        errs = _minimal_errors(data, schema)
    else:
        cls = jsonschema.validators.validator_for(schema)
        errs = [f"{_json_path(e.absolute_path)}: {e.message}" for e in cls(schema).iter_errors(data)]
    if errs:
        raise SchemaValidationError("structured output does not match the schema: " + "; ".join(errs[:5]), data)


# ---------------------------------------------------------------- クライアント

Messages = str | Sequence[Mapping[str, Any]]
System = str | Sequence[Mapping[str, Any]] | None


@dataclass
class _Reply:
    status: int
    headers: Mapping[str, str]
    body: bytes


class LLMClient:
    """Anthropic Messages API 互換エンドポイントのクライアント.

    >>> client = LLMClient("claude-haiku-4-5", system="あなたは校正係です。", max_tokens=512)  # doctest: +SKIP
    >>> client.complete("次の文の誤字を直して: ...").text  # doctest: +SKIP
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        auth_token: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 1024,
        temperature: float | None = None,
        system: System = None,
        cache_system: bool = False,
        masker: Masker | None = None,
        timeout: float = 120.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        backoff_max: float = 60.0,
        record_path: str | Path | None = None,
        record_content: str = "auto",
        extra_headers: Mapping[str, str] | None = None,
        opener: Callable[..., Any] = safe_urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ):
        """
        api_key / auth_token: 省略時は環境変数 ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN。
        system: 既定のシステムプロンプト（文字列か content ブロックの list）。
        cache_system=True で cache_control を付ける。
        masker: 指定すると、送る前に system と messages の本文を伏せる
            （text、tool_use の input、tool_result の content）。
        record_path: 指定すると、受け取った応答を JSONL に追記する（FakeLLM.from_jsonl で再生できる）。
            **注意**: 応答の本文（モデルが返した文章・構造化出力）がファイルに残る。
            業務の文書を扱うときは置き場所に気をつけ、コミットしない。
        record_content: record_path に書く本文の扱い。
            "auto"（既定）: masker があれば "masked"、無ければ "raw"。
            "masked": 本文（text・tool_use の input の文字列など）を masker で伏せてから書く（masker が要る）。
            "omit": 本文を書かない（text は `<text N chars>`、tool_use の input は {}）。
                トークン数などのメタ情報だけ残る。
            "raw": 受け取ったまま書く。
        """
        self.model = model
        self.api_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
        self.auth_token = auth_token if auth_token is not None else os.environ.get("ANTHROPIC_AUTH_TOKEN")
        self.base_url = (base_url or os.environ.get("ANTHROPIC_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.max_tokens, self.temperature = max_tokens, temperature
        self.system, self.cache_system, self.masker = system, cache_system, masker
        self.timeout, self.max_retries = timeout, max_retries
        self.backoff_base, self.backoff_max = backoff_base, backoff_max
        self.record_path = Path(record_path) if record_path else None
        if record_content not in RECORD_CONTENT_MODES:
            raise ValueError(f"record_content must be one of {RECORD_CONTENT_MODES}: {record_content!r}")
        if record_content == "masked" and masker is None:
            raise ValueError('record_content="masked" needs a masker')
        if record_content == "auto":
            record_content = "masked" if masker is not None else "raw"
        self.record_content = record_content
        self.extra_headers = dict(extra_headers or {})
        self._opener, self._sleep = opener, sleep

    # ---- 応答の記録

    def _record_view(self, data: Mapping[str, Any]) -> Any:
        """record_path に書く形. record_content に従って content の本文を伏せる・省く（data 自体は変えない）."""
        if self.record_content == "raw" or not isinstance(data.get("content"), list):
            return data
        return {**data, "content": [self._record_block(b) for b in data["content"]]}

    def _record_block(self, block: Any) -> Any:
        if not isinstance(block, dict):
            return block
        if self.record_content == "omit":
            out = dict(block)
            for k, v in block.items():
                if k in _RECORD_META_KEYS:
                    continue
                if isinstance(v, str):
                    out[k] = describe_text(v)
                elif isinstance(v, dict):
                    out[k] = {}
                elif isinstance(v, list):
                    out[k] = []
            return out
        assert self.masker is not None  # "masked" のときは __init__ で確かめている
        return {k: v if k in _RECORD_META_KEYS else _mask_deep(v, self.masker) for k, v in block.items()}

    # ---- 公開 API

    def complete(
        self,
        messages: Messages,
        *,
        system: System = None,
        cache_system: bool | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: Mapping[str, Any] | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> Completion:
        """1 回呼び出す. messages は文字列（user の 1 発言）か messages の list.

        system を省略するとクライアントの system を使う。出力が max_tokens で切れたら MaxTokensError。
        """
        body = self._body(messages, system, cache_system, max_tokens, temperature, tools, tool_choice, extra)
        start = time.monotonic()
        reply = self._send(body)
        duration_ms = int((time.monotonic() - start) * 1000)
        data = _parse_reply(reply)
        if self.record_path:
            with self.record_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(self._record_view(data), ensure_ascii=False) + "\n")
        model = str(data.get("model") or body["model"])
        usage = Usage.from_api(data.get("usage"))
        content = list(data.get("content") or [])
        comp = Completion(
            text="".join(b.get("text", "") for b in content if b.get("type") == "text"),
            content=content,
            stop_reason=data.get("stop_reason"),
            model=model,
            usage=usage,
            request_id=_header(reply.headers, "request-id") or data.get("id"),
            duration_ms=duration_ms,
            cost_usd=usage.cost(model),
            raw=data,
        )
        self._log_call(comp)
        if comp.stop_reason == "max_tokens":
            raise MaxTokensError(comp)
        return comp

    def complete_json(
        self,
        messages: Messages,
        *,
        schema: Mapping[str, Any],
        name: str = "record",
        description: str = "Return the result as the tool input. Follow the input schema exactly.",
        **kw: Any,
    ) -> Any:
        """構造化出力. スキーマを input_schema にしたツールを 1 つだけ渡して使わせ、その入力を検証して返す.

        結果がスキーマに合わなければ SchemaValidationError（jsonschema があれば完全な検証、無ければ簡易検査）。
        kw は complete() と同じ（system, max_tokens 等）。ただし tools と tool_choice はこのメソッドが決めるので、
        渡すと ValueError（自分のツールを使うときは complete() を呼ぶ）。
        """
        reserved = sorted(k for k in ("tools", "tool_choice") if k in kw)
        if reserved:
            raise ValueError(
                f"complete_json() does not accept {', '.join(reserved)}: it sends exactly one tool built from schema= "
                "and forces it. To pass your own tools, call complete() instead"
            )
        tool = {"name": name, "description": description, "input_schema": dict(schema)}
        comp = self.complete(messages, tools=[tool], tool_choice={"type": "tool", "name": name}, **kw)
        for block in comp.content:
            if block.get("type") == "tool_use" and block.get("name") == name:
                data = block.get("input")
                validate(data, schema)
                return data
        raise SchemaValidationError(f"the model did not call the tool {name!r} (stop_reason={comp.stop_reason})")

    # ---- 内部

    def _body(
        self,
        messages: Messages,
        system: System,
        cache_system: bool | None,
        max_tokens: int | None,
        temperature: float | None,
        tools: Sequence[Mapping[str, Any]] | None,
        tool_choice: Mapping[str, Any] | None,
        extra: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        msgs = [{"role": "user", "content": messages}] if isinstance(messages, str) else [dict(m) for m in messages]
        sys_ = system if system is not None else self.system
        cache = self.cache_system if cache_system is None else cache_system
        if self.masker:
            msgs = [{**m, "content": self.masker.content(m.get("content"))} for m in msgs]
            sys_ = self.masker.content(sys_) if sys_ is not None else None
        body: dict[str, Any] = {"model": self.model, "max_tokens": max_tokens or self.max_tokens, "messages": msgs}
        if sys_:
            if cache and isinstance(sys_, str):
                body["system"] = [cached_text(sys_)]
            elif cache:
                blocks = [dict(b) for b in sys_]
                blocks[-1].setdefault("cache_control", {"type": "ephemeral"})  # 最後のブロックまでをキャッシュする
                body["system"] = blocks
            else:
                body["system"] = sys_ if isinstance(sys_, str) else [dict(b) for b in sys_]
        temp = temperature if temperature is not None else self.temperature
        if temp is not None:
            body["temperature"] = temp
        if tools:
            body["tools"] = [dict(t) for t in tools]
        if tool_choice:
            body["tool_choice"] = dict(tool_choice)
        extra = dict(extra or {})
        if self.masker:  # extra で渡した messages / system も伏せる（伏せた後に上書きされて素通りしないように）
            if "messages" in extra and isinstance(extra["messages"], (list, tuple)):
                extra["messages"] = [
                    {**m, "content": self.masker.content(m.get("content"))} if isinstance(m, Mapping) else m
                    for m in extra["messages"]
                ]
            if "system" in extra and extra["system"] is not None:
                extra["system"] = self.masker.content(extra["system"])
        body.update(extra)
        return body

    def _headers(self) -> dict[str, str]:
        h = {"content-type": "application/json", "accept": "application/json", "anthropic-version": ANTHROPIC_VERSION}
        if self.api_key:
            h["x-api-key"] = self.api_key
        if self.auth_token:
            h["authorization"] = f"Bearer {self.auth_token}"
        if not (self.api_key or self.auth_token):
            raise LLMError("no API key: set ANTHROPIC_API_KEY (or pass api_key=)")
        return {**h, **self.extra_headers}

    def _delay(self, attempt: int, retry_after: str | None) -> float:
        """retry-after があれば優先し、無ければ（不正な値・nan・inf・負も）指数バックオフ.

        どちらも backoff_max までで、ゆらぎを足す. 計算は httpclient.retry_delay（HttpClient と共通）.
        """
        return retry_delay(attempt, retry_after, base=self.backoff_base, cap=self.backoff_max, jitter=True)

    def _send(self, body: Mapping[str, Any]) -> _Reply:
        url = self.base_url + "/v1/messages"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = self._headers()
        for attempt in range(self.max_retries + 1):
            last = attempt == self.max_retries
            req = urllib.request.Request(url, data=data, method="POST", headers=headers)
            try:
                with self._opener(req, timeout=self.timeout) as resp:
                    return _Reply(resp.status, dict(resp.headers), resp.read())
            except urllib.error.HTTPError as e:
                hdrs = e.headers or {}
                err_type, message = _parse_error(e)
                should = _header(hdrs, "x-should-retry")
                retryable = (e.code == 429 or e.code >= 500) if should is None else should.lower() == "true"
                if not retryable or last:
                    raise APIStatusError(e.code, err_type, message, _header(hdrs, "request-id")) from e
                delay = self._delay(attempt, _header(hdrs, "retry-after"))
                log.info("llm: HTTP %d %s, retrying in %.1fs (attempt %d)", e.code, err_type, delay, attempt + 1)
            # 接続拒否・切断・タイムアウト・本文の途中切れ・壊れた応答（HTTPException は OSError ではない）
            except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
                permanent = _is_permanent_connection_error(e)
                if last or permanent:
                    reason = getattr(e, "reason", None) or e
                    kind = "permanent " if permanent else ""
                    raise APIConnectionError(f"{kind}connection error: {type(e).__name__}: {reason}") from e
                delay = self._delay(attempt, None)
                log.info(
                    "llm: connection error (%s), retrying in %.1fs (attempt %d)", type(e).__name__, delay, attempt + 1
                )
            self._sleep(delay)
        raise LLMError("unreachable")  # pragma: no cover

    def _log_call(self, c: Completion) -> None:
        rec = {
            "model": c.model,
            "input_tokens": c.usage.input_tokens,
            "output_tokens": c.usage.output_tokens,
            "cache_creation_input_tokens": c.usage.cache_creation_input_tokens,
            "cache_read_input_tokens": c.usage.cache_read_input_tokens,
            "duration_ms": c.duration_ms,
            "request_id": c.request_id,
            "cost_usd": c.cost_usd,
            "stop_reason": c.stop_reason,
        }
        cost = "unknown" if c.cost_usd is None else f"{c.cost_usd:.6f}"
        log.info(
            "llm call model=%s in=%d out=%d cache_write=%d cache_read=%d duration_ms=%d request_id=%s cost_usd=%s",
            c.model,
            c.usage.input_tokens,
            c.usage.output_tokens,
            c.usage.cache_creation_input_tokens,
            c.usage.cache_read_input_tokens,
            c.duration_ms,
            c.request_id,
            cost,
            extra={"llm": rec},
        )


def _header(headers: Mapping[str, str] | Any, name: str) -> str | None:
    for k, v in headers.items() if hasattr(headers, "items") else []:
        if k.lower() == name:
            return v
    return None


def _parse_reply(reply: _Reply) -> dict[str, Any]:
    """成功の応答の本文を読む. JSON のオブジェクトでなければ（プロキシのページ等）LLMError.

    本文はログにも例外にも出さない（状態・request-id・content-type・長さだけ）.
    """
    request_id = _header(reply.headers, "request-id")
    where = f"HTTP {reply.status}" + (f" (request-id {request_id})" if request_id else "")
    try:
        data = json.loads(reply.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        ctype = _header(reply.headers, "content-type") or "unknown"
        raise LLMError(
            f"the API returned a non-JSON body ({where}, content-type {ctype}, {len(reply.body)} bytes)"
        ) from e
    if not isinstance(data, dict):
        raise LLMError(f"the API returned JSON that is not an object ({where}, {type(data).__name__})")
    return data


def _parse_error(e: urllib.error.HTTPError) -> tuple[str, str]:
    """{"type": "error", "error": {"type": ..., "message": ...}} を読む（本文はログに出さない）."""
    try:
        err = json.loads(e.read().decode("utf-8")).get("error") or {}
        return str(err.get("type") or "error"), str(err.get("message") or e.reason or "")
    except Exception:  # noqa: BLE001 — 本文が JSON でない（プロキシのエラーページ等）
        return "error", str(e.reason or "")


# ---------------------------------------------------------------- テスト用


class FakeLLM(LLMClient):
    """記録した応答を順に返すクライアント（通信しない）. テストを決定的にするために使う.

    応答は Messages API の応答 JSON（1 行 1 件. `record_path` で記録したもの）。エラーを再現したいときは
    {"status": 529, "headers": {"retry-after": "1"}, "body": {...}} の形で書く。通信の部分だけを差し替えているので、
    再試行・max_tokens の検出・検証・ログは本物と同じに動く。送ったリクエスト本文は `requests` に残る。
    """

    def __init__(self, responses: Iterable[Mapping[str, Any]], model: str = "claude-haiku-4-5", **kw: Any):
        kw.setdefault("api_key", "fake-key")
        kw.setdefault("sleep", lambda s: None)
        kw["opener"] = self._open
        super().__init__(model, **kw)
        self._responses = [dict(r) for r in responses]
        self.requests: list[dict[str, Any]] = []

    @classmethod
    def from_jsonl(cls, path: str | Path, **kw: Any) -> FakeLLM:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        return cls([json.loads(x) for x in lines if x.strip()], **kw)

    def _open(self, req: urllib.request.Request, timeout: float) -> Any:
        self.requests.append(json.loads(req.data or b"{}"))  # type: ignore[arg-type]
        if not self._responses:
            raise LLMError("FakeLLM: no more recorded responses")
        r = self._responses.pop(0)
        wrapped = "body" in r and "status" in r
        status = int(r["status"]) if wrapped else 200
        headers = {str(k): str(v) for k, v in (r.get("headers") or {}).items()} if wrapped else {}
        body = json.dumps(r["body"] if wrapped else r).encode("utf-8")
        if status >= 400:
            msg = email.message.Message()
            for k, v in headers.items():
                msg[k] = v
            raise urllib.error.HTTPError(req.full_url, status, "error", msg, io.BytesIO(body))
        return _FakeResponse(status, headers, body)


@dataclass
class _FakeResponse:
    status: int
    headers: Mapping[str, str]
    _body: bytes

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *a: Any) -> None:
        return None
