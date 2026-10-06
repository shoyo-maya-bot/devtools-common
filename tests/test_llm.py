"""devtools_common.llm のテスト.

127.0.0.1 の偽の Messages API サーバーか FakeLLM だけを使い、外部には通信しない。名前・アドレス・番号はすべて架空。
"""

from __future__ import annotations

import json
import logging
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from devtools_common import llm
from devtools_common.llm import (
    APIConnectionError,
    APIStatusError,
    FakeLLM,
    LLMClient,
    Masker,
    MaxTokensError,
    SchemaValidationError,
    Usage,
    cached_text,
    estimate_cost,
)

API_KEY = "sk-test-not-a-real-key-0000000000"


def message(text="こんにちは", *, stop_reason="end_turn", model="claude-haiku-4-5", usage=None, content=None):
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content if content is not None else [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "usage": usage or {"input_tokens": 100, "output_tokens": 20},
    }


def error(status, etype, msg="err", headers=None):
    return (status, headers or {}, {"type": "error", "error": {"type": etype, "message": msg}})


def tool_message(data, name="record"):
    return message(content=[{"type": "tool_use", "id": "toolu_1", "name": name, "input": data}], stop_reason="tool_use")


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        srv = self.server
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        with srv.lock:
            headers = {k.lower(): v for k, v in self.headers.items()}
            srv.seen.append({"path": self.path, "headers": headers, "body": body})
            item = srv.script.pop(0) if srv.script else (200, {}, message())
        if item == "drop":  # 応答を返さずに切断する
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        status, headers, payload = item if isinstance(item, tuple) else (200, {}, item)
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("request-id", f"req_{len(srv.seen)}")
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class FakeAPI:
    """script の順に応答する偽の Messages API. 要素は応答 JSON・(status, headers, json)・"drop"."""

    def __init__(self, *script):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.srv.daemon_threads = True
        self.srv.seen, self.srv.lock, self.srv.script = [], threading.Lock(), list(script)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.thread = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    @property
    def seen(self):
        return self.srv.seen

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()


class Sleeps(list):
    def __call__(self, s):
        self.append(s)


def client(url, sleeps=None, **kw):
    kw.setdefault("api_key", API_KEY)
    return LLMClient("claude-haiku-4-5", base_url=url, sleep=sleeps if sleeps is not None else Sleeps(), **kw)


# ---------------------------------------------------------------- 基本


def test_complete_sends_messages_request_and_parses_result():
    usage = {"input_tokens": 1000, "output_tokens": 200, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    with FakeAPI(message("答え", usage=usage)) as api:
        c = client(api.url, max_tokens=256).complete("質問")
    req = api.seen[0]
    assert req["path"] == "/v1/messages"
    assert req["headers"]["x-api-key"] == API_KEY
    assert req["headers"]["anthropic-version"] == "2023-06-01"
    assert req["body"] == {
        "model": "claude-haiku-4-5",
        "max_tokens": 256,
        "messages": [{"role": "user", "content": "質問"}],
    }
    assert c.text == "答え"
    assert c.stop_reason == "end_turn"
    assert c.usage == Usage(1000, 200, 0, 0)
    assert c.request_id == "req_1"
    assert c.cost_usd == pytest.approx((1000 * 1.0 + 200 * 5.0) / 1e6)
    assert c.duration_ms >= 0


def test_env_base_url_and_api_key(monkeypatch):
    with FakeAPI(message()) as api:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", api.url + "/")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env-not-real")
        LLMClient("claude-haiku-4-5").complete("hi")
    assert api.seen[0]["headers"]["x-api-key"] == "sk-from-env-not-real"


def test_default_base_url(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    assert LLMClient("m", api_key="x").base_url == "https://api.anthropic.com"


def test_auth_token_and_missing_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(llm.LLMError, match="ANTHROPIC_API_KEY"):
        LLMClient("m", base_url="http://127.0.0.1:9").complete("hi")
    with FakeAPI(message()) as api:
        LLMClient("m", base_url=api.url, auth_token="gw-token-not-real").complete("hi")
    assert api.seen[0]["headers"]["authorization"] == "Bearer gw-token-not-real"
    assert "x-api-key" not in api.seen[0]["headers"]


def test_temperature_and_system_options():
    with FakeAPI(message(), message()) as api:
        c = client(api.url, temperature=0.0, system="既定のシステム")
        c.complete("a")
        c.complete([{"role": "user", "content": "b"}], system="上書き", temperature=0.5, max_tokens=10)
    first, second = api.seen[0]["body"], api.seen[1]["body"]
    assert first["temperature"] == 0.0 and first["system"] == "既定のシステム"
    assert second["temperature"] == 0.5 and second["system"] == "上書き" and second["max_tokens"] == 10


def test_cache_control_on_system():
    long_doc = "仕様書の本文。" * 10
    with FakeAPI(message(), message(), message()) as api:
        c = client(api.url, system=long_doc, cache_system=True)
        c.complete("q1")
        c.complete("q2", system=[{"type": "text", "text": "前置き"}, {"type": "text", "text": long_doc}])
        c.complete("q3", cache_system=False)
    assert api.seen[0]["body"]["system"] == [{"type": "text", "text": long_doc, "cache_control": {"type": "ephemeral"}}]
    blocks = api.seen[1]["body"]["system"]
    assert "cache_control" not in blocks[0] and blocks[1]["cache_control"] == {"type": "ephemeral"}
    assert api.seen[2]["body"]["system"] == long_doc
    assert cached_text("x", ttl="1h")["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_cache_usage_is_reported_and_priced():
    usage = {"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 0}
    usage2 = {**usage, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 2000}
    with FakeAPI(message(usage=usage), message(usage=usage2)) as api:
        c = client(api.url, system="x", cache_system=True)
        a, b = c.complete("1"), c.complete("2")
    assert a.usage.cache_creation_input_tokens == 2000 and b.usage.cache_read_input_tokens == 2000
    assert a.cost_usd == pytest.approx((10 * 1.0 + 5 * 5.0 + 2000 * 1.25) / 1e6)
    assert b.cost_usd == pytest.approx((10 * 1.0 + 5 * 5.0 + 2000 * 0.10) / 1e6)


# ---------------------------------------------------------------- 再試行


def test_retry_on_529_honors_retry_after():
    sleeps = Sleeps()
    with FakeAPI(error(529, "overloaded_error", headers={"retry-after": "2"}), message("ok")) as api:
        c = client(api.url, sleeps, backoff_base=0.5).complete("hi")
    assert c.text == "ok" and len(api.seen) == 2
    assert len(sleeps) == 1 and 2.0 <= sleeps[0] <= 2.25  # retry-after + ゆらぎ（backoff_base / 2 まで）


def test_retry_after_is_bounded():
    sleeps = Sleeps()
    with FakeAPI(error(429, "rate_limit_error", headers={"retry-after": "9999"}), message()) as api:
        client(api.url, sleeps, backoff_base=0.5, backoff_max=5).complete("hi")
    assert 5.0 <= sleeps[0] <= 5.25


def test_retry_on_500_with_jittered_backoff():
    sleeps = Sleeps()
    with FakeAPI(error(500, "api_error"), error(503, "api_error"), message()) as api:
        client(api.url, sleeps, backoff_base=1.0).complete("hi")
    assert len(api.seen) == 3
    assert 0.5 <= sleeps[0] <= 1.0 and 1.0 <= sleeps[1] <= 2.0


def test_retries_are_bounded_and_raise_status_error():
    sleeps = Sleeps()
    with FakeAPI(*[error(429, "rate_limit_error", "slow down")] * 5) as api, pytest.raises(APIStatusError) as ei:
        client(api.url, sleeps, max_retries=2).complete("hi")
    assert len(api.seen) == 3 and len(sleeps) == 2
    assert ei.value.status == 429 and ei.value.error_type == "rate_limit_error"
    assert ei.value.request_id == "req_3"


@pytest.mark.parametrize("status,etype", [(400, "invalid_request_error"), (401, "authentication_error")])
def test_client_errors_are_not_retried(status, etype):
    with FakeAPI(error(status, etype), message()) as api, pytest.raises(APIStatusError) as ei:
        client(api.url).complete("hi")
    assert len(api.seen) == 1 and ei.value.status == status and ei.value.error_type == etype


def test_x_should_retry_header_wins():
    script = (error(500, "api_error", headers={"x-should-retry": "false"}), message())
    with FakeAPI(*script) as api, pytest.raises(APIStatusError):
        client(api.url).complete("hi")
    assert len(api.seen) == 1


def test_retry_after_dropped_connection():
    sleeps = Sleeps()
    with FakeAPI("drop", message("ok")) as api:
        assert client(api.url, sleeps).complete("hi").text == "ok"
    assert len(api.seen) == 2 and len(sleeps) == 1


def test_connection_refused_is_retried_then_raises():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # 誰も待ち受けていないポート
    sleeps = Sleeps()
    with pytest.raises(APIConnectionError):
        client(f"http://127.0.0.1:{port}", sleeps, max_retries=2).complete("hi")
    assert len(sleeps) == 2


# ---------------------------------------------------------------- max_tokens・構造化出力


def test_max_tokens_stop_raises():
    with FakeAPI(message("途中まで", stop_reason="max_tokens")) as api, pytest.raises(MaxTokensError) as ei:
        client(api.url).complete("長い文章を書いて")
    assert ei.value.completion.text == "途中まで"


SCHEMA = {
    "type": "object",
    "properties": {
        "severity": {"type": "string", "enum": ["MUST", "SHOULD", "MAY"]},
        "summary": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["severity", "summary"],
}


def test_complete_json_forces_tool_and_returns_input():
    data = {"severity": "MUST", "summary": "桁数が違う", "tags": ["設計"]}
    with FakeAPI(tool_message(data)) as api:
        out = client(api.url).complete_json("分類して", schema=SCHEMA)
    assert out == data
    body = api.seen[0]["body"]
    assert body["tools"] == [{"name": "record", "description": body["tools"][0]["description"], "input_schema": SCHEMA}]
    assert body["tool_choice"] == {"type": "tool", "name": "record"}


@pytest.mark.parametrize("use_jsonschema", [True, False])
@pytest.mark.parametrize(
    "bad",
    [
        {"summary": "必須の severity が無い"},
        {"severity": "LOW", "summary": "enum に無い"},
        {"severity": "MUST", "summary": 1},
        {"severity": "MUST", "summary": "x", "tags": ["a", 2]},
    ],
)
def test_complete_json_rejects_mismatch(bad, use_jsonschema, monkeypatch):
    if use_jsonschema:
        pytest.importorskip("jsonschema")
    else:
        monkeypatch.setitem(sys.modules, "jsonschema", None)  # 未導入と同じ（import が ImportError）
    with pytest.raises(SchemaValidationError) as ei:
        FakeLLM([tool_message(bad)]).complete_json("x", schema=SCHEMA)
    assert ei.value.data == bad


def test_complete_json_without_tool_call_raises():
    with pytest.raises(SchemaValidationError, match="did not call"):
        FakeLLM([message("ツールを使わずに答えた")]).complete_json("x", schema=SCHEMA)


def test_complete_json_max_tokens_raises():
    with pytest.raises(MaxTokensError):
        FakeLLM([{**tool_message({"severity": "MUST"}), "stop_reason": "max_tokens"}]).complete_json("x", schema=SCHEMA)


# ---------------------------------------------------------------- ログ・料金


def test_call_log_record(caplog):
    usage = {"input_tokens": 1000, "output_tokens": 10, "cache_creation_input_tokens": 3, "cache_read_input_tokens": 4}
    with (
        caplog.at_level(logging.INFO, logger="devtools_common.llm"),
        FakeAPI(message("秘密の本文", usage=usage)) as api,
    ):
        client(api.url).complete("送った本文")
    rec = next(r for r in caplog.records if hasattr(r, "llm"))
    assert rec.llm["model"] == "claude-haiku-4-5"
    assert (rec.llm["input_tokens"], rec.llm["output_tokens"]) == (1000, 10)
    assert (rec.llm["cache_creation_input_tokens"], rec.llm["cache_read_input_tokens"]) == (3, 4)
    assert rec.llm["request_id"] == "req_1" and rec.llm["duration_ms"] >= 0 and rec.llm["cost_usd"] > 0
    text = caplog.text
    assert "秘密の本文" not in text and "送った本文" not in text and API_KEY not in text


def test_estimate_cost_and_prefix_match():
    assert estimate_cost("claude-sonnet-4-5", 1_000_000, 1_000_000) == pytest.approx(18.0)
    assert estimate_cost("claude-sonnet-4-5-20250929", 1_000_000, 0) == pytest.approx(3.0)
    assert estimate_cost("claude-opus-4-1", 1_000_000, 0) == pytest.approx(15.0)
    assert estimate_cost("claude-opus-4-5", 1_000_000, 0) == pytest.approx(5.0)  # 「claude-opus-4」ではなく長い方に一致
    assert estimate_cost("claude-haiku-4-5", 0, 0, cache_read_input_tokens=1_000_000) == pytest.approx(0.10)


def test_unknown_model_warns_and_returns_none(caplog):
    with caplog.at_level(logging.WARNING, logger="devtools_common.llm"):
        assert estimate_cost("claude-opus-4-9", 10, 10) is None  # 既知の「claude-opus-4」に誤って当てない
        assert estimate_cost("my-local-model", 10, 10) is None
    assert "unknown model 'claude-opus-4-9'" in caplog.text and "unknown model 'my-local-model'" in caplog.text
    c = FakeLLM([message(model="vendor-model-x")], model="vendor-model-x").complete("x")
    assert c.cost_usd is None


# ---------------------------------------------------------------- 伏せ字


@pytest.mark.parametrize(
    "value",
    [
        "taro@example.com",
        "taro.yamada+qa@mail.example.co.jp",
        "ｔａｒｏ＠ｅｘａｍｐｌｅ．ｃｏｍ",
        "taro＠example.com",
        "090-1234-5678",
        "０９０－１２３４－５６７８",
        "090ー1234ー5678",
        "09012345678",
        "０９０１２３４５６７８",
        "090 1234 5678",
        "03(1234)5678",
        "03-1234-5678",
        "0120-123-456",
        "+81 90-1234-5678",
        "+819012345678",
        "＋８１－９０－１２３４－５６７８",
    ],
)
def test_masker_builtin(value):
    assert Masker().text(f"連絡先は{value}です") == f"連絡先は{llm.MASK}です"


@pytest.mark.parametrize(
    "value",
    ["2026-10-06", "応答時間が 3 秒と 5 秒で異なる", "注文番号 0001", "v1.2.3", "#12/c1001", "090-1234"],
)
def test_masker_keeps_ordinary_text(value):
    assert Masker().text(value) == value


def test_masker_extra_patterns_words_and_replacement():
    m = Masker(patterns=[r"EMP-\d{4}"], words=["架空商事"], replacement="[伏]")
    assert m("架空商事の EMP-1234 さん（taro@example.com）") == "[伏]の [伏] さん（[伏]）"
    assert m.content([{"type": "text", "text": "EMP-0001"}, {"type": "image"}]) == [
        {"type": "text", "text": "[伏]"},
        {"type": "image"},
    ]


def test_client_masks_before_sending():
    with FakeAPI(message()) as api:
        msgs = [{"role": "user", "content": [{"type": "text", "text": "架空商事 ０９０－１２３４－５６７８"}]}]
        client(api.url, masker=Masker(words=["架空商事"]), system="担当: hanako@example.org").complete(msgs)
    sent = json.dumps(api.seen[0]["body"], ensure_ascii=False)
    for secret in ("架空商事", "hanako@example.org", "０９０－１２３４－５６７８"):
        assert secret not in sent
    assert api.seen[0]["body"]["system"] == f"担当: {llm.MASK}"


# ---------------------------------------------------------------- FakeLLM・記録と再生


def test_record_and_replay(tmp_path):
    rec = tmp_path / "responses.jsonl"
    data = {"severity": "SHOULD", "summary": "表記ゆれ"}
    with FakeAPI(message("一回目"), tool_message(data)) as api:
        c = client(api.url, record_path=rec)
        assert c.complete("a").text == "一回目"
        assert c.complete_json("b", schema=SCHEMA) == data
    fake = FakeLLM.from_jsonl(rec)
    assert fake.complete("a").text == "一回目"
    assert fake.complete_json("b", schema=SCHEMA) == data
    assert [r["messages"][0]["content"] for r in fake.requests] == ["a", "b"]
    with pytest.raises(llm.LLMError, match="no more recorded responses"):
        fake.complete("c")


def test_fake_llm_replays_errors_through_retry(tmp_path):
    p = tmp_path / "r.jsonl"
    lines = [
        {
            "status": 529,
            "headers": {"retry-after": "3"},
            "body": {"type": "error", "error": {"type": "overloaded_error"}},
        },
        message("ok"),
    ]
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8")
    sleeps = Sleeps()
    fake = FakeLLM.from_jsonl(p, sleep=sleeps)
    assert fake.complete("x").text == "ok"
    assert len(fake.requests) == 2 and 3.0 <= sleeps[0] <= 3.5
    with pytest.raises(APIStatusError) as ei:
        FakeLLM([lines[0]], max_retries=0).complete("x")
    assert ei.value.status == 529 and ei.value.error_type == "overloaded_error"
