"""Transport tests (PZ-032): retry/backoff policy behavior and the
async HTTP transport with an injected fake opener (no real network)."""

from __future__ import annotations

import asyncio
import io
import json
import urllib.error

import pytest

from paiziq import AsyncHTTPTransport, RetryPolicy, TransportError


class FakeResponse:
    def __init__(self, status: int = 200, body: dict | None = None):
        self.status = status
        self._body = json.dumps(body or {"ok": True}).encode()
        self.headers = {"Content-Type": "application/json"}

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Scripted opener: pops one outcome per call. An outcome is either
    a FakeResponse, an int HTTP status, or an Exception to raise."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests: list = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, int):
            if outcome >= 400:
                raise urllib.error.HTTPError(
                    request.full_url, outcome, "err", {}, io.BytesIO(b'{"error": true}')
                )
            return FakeResponse(status=outcome)
        return outcome


NO_WAIT = RetryPolicy(max_attempts=3, base_delay_s=0, max_delay_s=0)


def transport(opener, retry=NO_WAIT) -> AsyncHTTPTransport:
    return AsyncHTTPTransport(
        "https://api.example.test", api_key="k-123", retry=retry, opener=opener
    )


def run(coro):
    return asyncio.run(coro)


# ── RetryPolicy ──────────────────────────────────────────────────────────────

def test_delay_grows_exponentially_and_is_capped():
    policy = RetryPolicy(max_attempts=5, base_delay_s=1.0, max_delay_s=4.0, jitter_ratio=0)
    assert [policy.delay_for(a) for a in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 4.0]


def test_delay_jitter_stays_within_ratio():
    policy = RetryPolicy(base_delay_s=1.0, jitter_ratio=0.5)
    low = policy.delay_for(1, rng=lambda: 0.0)   # jitter fully negative
    high = policy.delay_for(1, rng=lambda: 1.0)  # jitter fully positive
    assert low == pytest.approx(0.5)
    assert high == pytest.approx(1.5)


def test_retry_policy_validates_bounds():
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        RetryPolicy(jitter_ratio=2.0)
    with pytest.raises(ValueError):
        RetryPolicy(base_delay_s=-1)


# ── AsyncHTTPTransport ───────────────────────────────────────────────────────

def test_success_first_attempt():
    opener = FakeOpener(FakeResponse(body={"data": 1}))
    response = run(transport(opener).post("/v1/decisions", json_body={"payment_id": "p"}))
    assert response.status == 200
    assert response.json() == {"data": 1}
    assert len(opener.requests) == 1
    assert opener.requests[0].get_header("Authorization") == "Bearer k-123"


def test_retries_on_5xx_then_succeeds():
    opener = FakeOpener(500, 503, FakeResponse())
    response = run(transport(opener).get("/health"))
    assert response.status == 200
    assert len(opener.requests) == 3


def test_retries_on_connection_error_then_succeeds():
    opener = FakeOpener(urllib.error.URLError("refused"), FakeResponse())
    response = run(transport(opener).get("/health"))
    assert response.status == 200
    assert len(opener.requests) == 2


def test_retries_on_429_rate_limit():
    opener = FakeOpener(429, FakeResponse())
    response = run(transport(opener).get("/health"))
    assert response.status == 200
    assert len(opener.requests) == 2


def test_non_retryable_4xx_returned_without_retry():
    opener = FakeOpener(404)
    response = run(transport(opener).get("/v1/payments/pay_missing"))
    assert response.status == 404
    assert response.json() == {"error": True}
    assert len(opener.requests) == 1  # fail fast, no retries


def test_gives_up_after_max_attempts_with_status():
    opener = FakeOpener(500, 500, 500)
    with pytest.raises(TransportError) as excinfo:
        run(transport(opener).get("/health"))
    assert excinfo.value.status == 500
    assert "after 3 attempts" in str(excinfo.value)
    assert len(opener.requests) == 3


def test_gives_up_on_persistent_connection_errors():
    opener = FakeOpener(*[urllib.error.URLError("down")] * 3)
    with pytest.raises(TransportError) as excinfo:
        run(transport(opener).get("/health"))
    assert excinfo.value.status is None
    assert len(opener.requests) == 3


def test_max_attempts_one_never_retries():
    opener = FakeOpener(500)
    policy = RetryPolicy(max_attempts=1, base_delay_s=0)
    with pytest.raises(TransportError):
        run(transport(opener, retry=policy).get("/health"))
    assert len(opener.requests) == 1


def test_json_body_and_url_joining():
    opener = FakeOpener(FakeResponse())
    run(transport(opener).post("v1/traces", json_body={"spans": []}))
    request = opener.requests[0]
    assert request.full_url == "https://api.example.test/v1/traces"
    assert json.loads(request.data.decode()) == {"spans": []}
