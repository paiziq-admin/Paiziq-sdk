"""Smoke-test a running Paiziq backend (local container or hosted).

Standard library only, so it runs from any machine or CI runner without the
SDK installed. Checks the facts a deployment must satisfy before the
dashboard or an agent is pointed at it:

1. ``GET /health`` returns JSON ``{"status": "ok"}`` without credentials.
   An HTML answer means the URL is a static site, not the API.
2. The supplied key can perform the same one-row read the dashboard login
   uses (``GET /v1/agents?limit=1``) and receives the success envelope.
3. A wrong key is rejected with 403 and a missing key with 401.
4. Optionally, a CORS preflight from the dashboard origin is allowed.

Usage::

    PAIZIQ_ENDPOINT=https://api.example PAIZIQ_API_KEY=... \
        python3 scripts/smoke_backend.py [--origin https://dashboard.example]

Exit status is 0 only when every check passes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

LOGIN_PROBE_PATH = "/v1/agents?limit=1"
PREFLIGHT_PATH = "/v1/agents"


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class RawResponse:
    status: int
    body: bytes
    headers: dict[str, str]

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


def _request(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[dict[str, str]] = None,
    timeout_s: float = 10.0,
) -> RawResponse:
    request = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return RawResponse(
                status=response.status,
                body=response.read(),
                headers={k.lower(): v for k, v in response.headers.items()},
            )
    except urllib.error.HTTPError as exc:
        return RawResponse(
            status=exc.code,
            body=exc.read() if hasattr(exc, "read") else b"",
            headers={k.lower(): v for k, v in (exc.headers or {}).items()},
        )


def _looks_like_html(body: bytes) -> bool:
    head = body.lstrip()[:64].lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def check_health(base_url: str, timeout_s: float) -> CheckResult:
    try:
        response = _request(f"{base_url}/health", timeout_s=timeout_s)
    except (urllib.error.URLError, OSError) as exc:
        return CheckResult("health", False, f"cannot reach {base_url}/health: {exc}")
    if _looks_like_html(response.body):
        return CheckResult(
            "health",
            False,
            "received HTML; this URL is a static site or SPA route, not the backend origin",
        )
    try:
        payload = response.json()
    except ValueError:
        return CheckResult("health", False, f"HTTP {response.status} with non-JSON body")
    if response.status == 200 and payload == {"status": "ok"}:
        return CheckResult("health", True, 'HTTP 200 {"status": "ok"}')
    return CheckResult("health", False, f"HTTP {response.status} {payload!r}")


def check_login_probe(base_url: str, api_key: str, timeout_s: float) -> CheckResult:
    response = _request(
        f"{base_url}{LOGIN_PROBE_PATH}",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout_s=timeout_s,
    )
    try:
        payload = response.json()
    except ValueError:
        return CheckResult("login_probe", False, f"HTTP {response.status} with non-JSON body")
    if response.status == 200 and isinstance(payload, dict) and payload.get("success") is True:
        total = (payload.get("meta") or {}).get("total")
        return CheckResult("login_probe", True, f"HTTP 200 success envelope; agents total={total}")
    if response.status in (401, 403):
        return CheckResult(
            "login_probe", False, f"HTTP {response.status}: the supplied key is not accepted"
        )
    return CheckResult("login_probe", False, f"HTTP {response.status} {payload!r}")


def check_wrong_key_rejected(base_url: str, timeout_s: float) -> CheckResult:
    response = _request(
        f"{base_url}{LOGIN_PROBE_PATH}",
        headers={"Authorization": "Bearer smoke-invalid-key"},
        timeout_s=timeout_s,
    )
    if response.status == 403:
        return CheckResult("wrong_key_rejected", True, "HTTP 403 for an unknown key")
    return CheckResult(
        "wrong_key_rejected", False, f"expected HTTP 403 for an unknown key, got {response.status}"
    )


def check_missing_key_rejected(base_url: str, timeout_s: float) -> CheckResult:
    response = _request(f"{base_url}{LOGIN_PROBE_PATH}", timeout_s=timeout_s)
    if response.status == 401:
        return CheckResult("missing_key_rejected", True, "HTTP 401 without Authorization")
    return CheckResult(
        "missing_key_rejected",
        False,
        f"expected HTTP 401 without Authorization, got {response.status}",
    )


def check_cors_preflight(base_url: str, origin: str, timeout_s: float) -> CheckResult:
    response = _request(
        f"{base_url}{PREFLIGHT_PATH}",
        method="OPTIONS",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
        timeout_s=timeout_s,
    )
    allowed = response.headers.get("access-control-allow-origin")
    if response.status == 200 and allowed == origin:
        return CheckResult("cors_preflight", True, f"origin {origin} allowed")
    return CheckResult(
        "cors_preflight",
        False,
        f"HTTP {response.status}; access-control-allow-origin={allowed!r}; "
        f"set PAIZIQ_CORS_ORIGINS to include {origin}",
    )


def run_smoke(
    base_url: str,
    api_key: str,
    origin: Optional[str] = None,
    timeout_s: float = 10.0,
    authenticated_probe: bool = True,
) -> list[CheckResult]:
    base_url = base_url.rstrip("/")
    results = [check_health(base_url, timeout_s)]
    if not results[0].passed:
        return results
    if authenticated_probe:
        results.append(check_login_probe(base_url, api_key, timeout_s))
    results.append(check_wrong_key_rejected(base_url, timeout_s))
    results.append(check_missing_key_rejected(base_url, timeout_s))
    if origin:
        results.append(check_cors_preflight(base_url, origin.rstrip("/"), timeout_s))
    return results


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a running Paiziq backend")
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("PAIZIQ_ENDPOINT", "http://127.0.0.1:8800"),
        help="backend base URL (env PAIZIQ_ENDPOINT)",
    )
    parser.add_argument(
        "--origin",
        default=os.environ.get("PAIZIQ_DASHBOARD_ORIGIN") or None,
        help="dashboard origin to preflight (env PAIZIQ_DASHBOARD_ORIGIN)",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--unauthenticated", action="store_true",
        help="check health, invalid/missing key rejection and CORS without a valid API key",
    )
    args = parser.parse_args(argv)

    api_key = os.environ.get("PAIZIQ_API_KEY", "")
    if not api_key and not args.unauthenticated:
        print("PAIZIQ_API_KEY is required (never pass secrets as CLI arguments)", file=sys.stderr)
        return 2

    results = run_smoke(
        args.endpoint, api_key, args.origin, args.timeout,
        authenticated_probe=not args.unauthenticated,
    )
    width = max(len(result.name) for result in results)
    for result in results:
        marker = "PASS" if result.passed else "FAIL"
        print(f"{marker}  {result.name.ljust(width)}  {result.detail}")
    failed = [result for result in results if not result.passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed against {args.endpoint}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
