"""HTTP transports with bounded exponential backoff (stdlib only).

`AsyncHTTPTransport` (PZ-032) runs blocking `urllib` calls in an
executor so agents on asyncio never block their event loop. Both
transports share one `RetryPolicy`: retry on 429/5xx responses and
connection errors, back off exponentially with jitter, and raise
`TransportError` only after the policy is exhausted. Non-retryable
HTTP statuses (4xx other than 429) fail fast.

The transports are used for control-plane calls where the caller
decides how failures map to verdicts (see failure modes, PZ-035);
fire-and-forget observability keeps using the batching exporters.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import random
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("paiziq.transport")

_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class TransportError(Exception):
    """Raised when a request fails permanently (after retries)."""

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential backoff with jitter, shared by both transports."""

    max_attempts: int = 3
    base_delay_s: float = 0.5
    max_delay_s: float = 30.0
    jitter_ratio: float = 0.1  # +/- share of the delay randomized
    retry_statuses: frozenset[int] = field(default_factory=lambda: _RETRYABLE_STATUSES)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {self.max_attempts!r}")
        if self.base_delay_s < 0 or self.max_delay_s < 0:
            raise ValueError("delays must be >= 0")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError(f"jitter_ratio must be in [0, 1], got {self.jitter_ratio!r}")

    def should_retry_status(self, status: int) -> bool:
        return status in self.retry_statuses

    def delay_for(self, attempt: int, rng: Callable[[], float] = random.random) -> float:
        """Delay before retry number `attempt` (1-based), capped and jittered."""
        delay = min(self.max_delay_s, self.base_delay_s * (2 ** (attempt - 1)))
        jitter = delay * self.jitter_ratio * (2 * rng() - 1)
        return max(0.0, delay + jitter)


@dataclass
class TransportResponse:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        return _json.loads(self.body.decode("utf-8"))


def _attempt_request(
    url: str,
    method: str,
    body: Optional[bytes],
    headers: dict[str, str],
    timeout_s: float,
    opener: Callable[..., Any],
) -> TransportResponse:
    """One HTTP attempt. HTTP error statuses return a TransportResponse;
    connection-level failures raise (and are retried by the caller)."""
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with opener(request, timeout=timeout_s) as resp:
            return TransportResponse(
                status=getattr(resp, "status", 200),
                body=resp.read(),
                headers=dict(getattr(resp, "headers", {}) or {}),
            )
    except urllib.error.HTTPError as exc:  # non-2xx with a response
        return TransportResponse(
            status=exc.code,
            body=exc.read() if hasattr(exc, "read") else b"",
            headers=dict(exc.headers or {}),
        )


class _BaseTransport:
    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        timeout_s: float = 10.0,
        retry: Optional[RetryPolicy] = None,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.retry = retry or RetryPolicy()
        self._opener = opener

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _headers(self, extra: Optional[dict[str, str]]) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "User-Agent": "paiziq-sdk"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if extra:
            headers.update(extra)
        return headers

    @staticmethod
    def _encode(json_body: Optional[Any]) -> Optional[bytes]:
        if json_body is None:
            return None
        return _json.dumps(json_body, default=str).encode("utf-8")


class AsyncHTTPTransport(_BaseTransport):
    """Asyncio-friendly transport: blocking I/O runs in the default
    executor, backoff sleeps use `asyncio.sleep` (PZ-032)."""

    async def request(
        self,
        method: str,
        path: str,
        json_body: Optional[Any] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> TransportResponse:
        url = self._url(path)
        body = self._encode(json_body)
        all_headers = self._headers(headers)
        loop = asyncio.get_running_loop()
        last_error: Optional[str] = None
        last_status: Optional[int] = None

        for attempt in range(1, self.retry.max_attempts + 1):
            try:
                response = await loop.run_in_executor(
                    None,
                    lambda: _attempt_request(
                        url, method, body, all_headers, self.timeout_s, self._opener
                    ),
                )
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                last_error, last_status = str(exc), None
                logger.warning("paiziq transport attempt %d failed: %s", attempt, exc)
            else:
                if not self.retry.should_retry_status(response.status):
                    return response
                last_error = f"HTTP {response.status}"
                last_status = response.status
                logger.warning(
                    "paiziq transport attempt %d got retryable status %d",
                    attempt, response.status,
                )
            if attempt < self.retry.max_attempts:
                await asyncio.sleep(self.retry.delay_for(attempt))

        raise TransportError(
            f"{method} {url} failed after {self.retry.max_attempts} attempts: {last_error}",
            status=last_status,
        )

    async def get(self, path: str, **kw: Any) -> TransportResponse:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, json_body: Optional[Any] = None, **kw: Any) -> TransportResponse:
        return await self.request("POST", path, json_body=json_body, **kw)
