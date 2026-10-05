"""外部 API 連携の共通クライアント（標準ライブラリのみ）.

- レート制限: トークンバケットで 1 秒あたりのリクエスト数を制御
- リトライ: 429 / 5xx / ネットワークエラーを指数バックオフで再試行（Retry-After を優先）
  - 冪等なメソッド（GET / HEAD / PUT / DELETE / OPTIONS / TRACE）だけを再試行する
  - POST / PATCH は二重登録を防ぐため、サーバーに届いていないことが確実なとき（接続拒否・名前解決の失敗）と
    Retry-After 付きの 429 のときだけ再試行する。`idempotent=True` か Idempotency-Key ヘッダーで常に再試行できる
- エラーハンドリング: 最終的に失敗したら HttpError を送出（本文はログに出さない）
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .log import get_logger

log = get_logger(__name__)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE", "OPTIONS", "TRACE"})
IDEMPOTENCY_KEY_HEADER = "idempotency-key"

# 送信前に失敗したことが確実なエラー（URLError.reason）。これらはサーバーに届いていない
_NOT_SENT_ERRORS: tuple[type[BaseException], ...] = (ConnectionRefusedError, socket.gaierror)


class HttpError(Exception):
    def __init__(self, status: int | None, message: str):
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status


@dataclass
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class RateLimiter:
    """トークンバケット。rate_per_sec 回/秒、burst 回まで連続実行可."""

    def __init__(
        self,
        rate_per_sec: float,
        burst: int = 1,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be positive")
        self.rate, self.capacity = rate_per_sec, float(burst)
        self.tokens = float(burst)
        self._clock, self._sleep = clock, sleep
        self._last = clock()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            self.tokens = min(self.capacity, self.tokens + (now - self._last) * self.rate)
            self._last = now
            if self.tokens < 1:
                wait = (1 - self.tokens) / self.rate
                self._sleep(wait)
                self._last = self._clock()
                self.tokens = 0.0
            else:
                self.tokens -= 1


@dataclass
class HttpClient:
    base_url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    rate_per_sec: float = 5.0
    max_retries: int = 3
    backoff_base: float = 0.5
    backoff_max: float = 30.0
    timeout: float = 30.0
    opener: Callable[..., Any] = urllib.request.urlopen
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        self._limiter = RateLimiter(self.rate_per_sec, sleep=self.sleep)

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), self.backoff_max)
            except ValueError:
                pass
        return min(self.backoff_base * (2**attempt), self.backoff_max)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        headers: Mapping[str, str] | None = None,
        idempotent: bool | None = None,
    ) -> Response:
        """リクエストを送る.

        idempotent: None（既定）はメソッドから判断する（GET / PUT / DELETE 等は冪等、POST / PATCH は非冪等）。
        Idempotency-Key ヘッダーがあれば冪等とみなす。True で常に再試行、False で届いた可能性があれば再試行しない。
        """
        url = urllib.parse.urljoin(self.base_url.rstrip("/") + "/", path.lstrip("/"))
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = None
        merged = {"Accept": "application/json", **self.headers, **(headers or {})}
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            merged.setdefault("Content-Type", "application/json")
        method = method.upper()
        if idempotent is None:
            idempotent = method in IDEMPOTENT_METHODS or any(k.lower() == IDEMPOTENCY_KEY_HEADER for k in merged)

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._limiter.acquire()
            req = urllib.request.Request(url, data=data, method=method, headers=merged)
            try:
                with self.opener(req, timeout=self.timeout) as resp:
                    return Response(resp.status, dict(resp.headers), resp.read())
            except urllib.error.HTTPError as e:
                last_error = HttpError(e.code, e.reason or "")
                retry_after = e.headers.get("Retry-After") if e.headers else None
                # 429 + Retry-After は「処理せずに断った」という意味なので非冪等でも再試行してよい
                retryable = e.code in RETRY_STATUSES and (idempotent or (e.code == 429 and bool(retry_after)))
                if not retryable or attempt == self.max_retries:
                    self._log_not_retried(method, path, idempotent, e.code in RETRY_STATUSES)
                    raise last_error from e
                delay = self._backoff(attempt, retry_after)
            except (urllib.error.URLError, TimeoutError) as e:
                reason = getattr(e, "reason", e)
                last_error = HttpError(None, f"network error: {reason}")
                # URLError でも送信中のタイムアウト等は届いた可能性がある。読み取りタイムアウト（TimeoutError）も同じ
                not_sent = isinstance(e, urllib.error.URLError) and isinstance(reason, _NOT_SENT_ERRORS)
                if not (idempotent or not_sent) or attempt == self.max_retries:
                    self._log_not_retried(method, path, idempotent, True)
                    raise last_error from e
                delay = self._backoff(attempt, None)
            log.info("retrying %s %s (attempt %d) in %.1fs", method, path, attempt + 1, delay)
            self.sleep(delay)
        raise last_error or HttpError(None, "request failed")  # pragma: no cover

    @staticmethod
    def _log_not_retried(method: str, path: str, idempotent: bool, transient: bool) -> None:
        if transient and not idempotent:
            log.warning(
                "not retrying %s %s: the server may have received it (pass idempotent=True or an Idempotency-Key)",
                method,
                path,
            )

    def get(self, path: str, **kw: Any) -> Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Response:
        return self.request("POST", path, **kw)
