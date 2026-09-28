"""外部 API 連携の共通クライアント（標準ライブラリのみ）.

- レート制限: トークンバケットで 1 秒あたりのリクエスト数を制御
- リトライ: 429 / 5xx / ネットワークエラーを指数バックオフで再試行（Retry-After を優先）
- エラーハンドリング: 最終的に失敗したら HttpError を送出（本文はログに出さない）
"""

from __future__ import annotations

import json
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
    ) -> Response:
        url = urllib.parse.urljoin(self.base_url.rstrip("/") + "/", path.lstrip("/"))
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = None
        merged = {"Accept": "application/json", **self.headers, **(headers or {})}
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            merged.setdefault("Content-Type", "application/json")

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._limiter.acquire()
            req = urllib.request.Request(url, data=data, method=method.upper(), headers=merged)
            try:
                with self.opener(req, timeout=self.timeout) as resp:
                    return Response(resp.status, dict(resp.headers), resp.read())
            except urllib.error.HTTPError as e:
                last_error = HttpError(e.code, e.reason or "")
                if e.code not in RETRY_STATUSES or attempt == self.max_retries:
                    raise last_error from e
                delay = self._backoff(attempt, e.headers.get("Retry-After") if e.headers else None)
            except (urllib.error.URLError, TimeoutError) as e:
                last_error = HttpError(None, f"network error: {getattr(e, 'reason', e)}")
                if attempt == self.max_retries:
                    raise last_error from e
                delay = self._backoff(attempt, None)
            log.info("retrying %s %s (attempt %d) in %.1fs", method.upper(), path, attempt + 1, delay)
            self.sleep(delay)
        raise last_error or HttpError(None, "request failed")  # pragma: no cover

    def get(self, path: str, **kw: Any) -> Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Response:
        return self.request("POST", path, **kw)
