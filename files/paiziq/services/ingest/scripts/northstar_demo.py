"""Run the Northstar payment-agent demo against a deployed Paiziq backend.

The Northstar demo is the deterministic three-payment workflow from
``tests/e2e_support/scenario.py`` executed by the real SDK
(``SimulatedPaymentAgent``): one approved and executed payment, one that
opens a human review, and one rejected by the merchant blocklist, each with
a decision, risk flags, policy version, and an exported trace.

Compared with ``make e2e-seed`` (local only), this runner:

- refuses to start unless ``/health`` returns backend JSON (not static HTML);
- issues a *read-only* managed API key for the demo environment so the
  bootstrap admin key never has to be typed into a browser;
- verifies that key with the exact probe the dashboard login performs;
- optionally checks the CORS preflight from the dashboard origin;
- writes a JSON report (without any secret) for the walkthrough.

Usage::

    PAIZIQ_ENDPOINT=https://<backend> PAIZIQ_API_KEY=<admin key> \
        python3 scripts/northstar_demo.py --dashboard-url https://<dashboard>

The plaintext dashboard key is printed exactly once; the backend never
returns it again.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_INGEST = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = _INGEST.parents[1]
for candidate in (str(_INGEST), str(_INGEST / "tests")):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from e2e_support.scenario import TRANSACTIONS  # noqa: E402
from e2e_support.simulated_agent import ControlPlaneError, SimulatedPaymentAgent  # noqa: E402
from paiziq.transport import RetryPolicy, SyncHTTPTransport  # noqa: E402

DEFAULT_REPORT = _PROJECT_ROOT / ".e2e" / "northstar-run.json"
LOGIN_PROBE_PATH = "/v1/agents"


class DemoError(RuntimeError):
    """The demo could not run against this backend."""


@dataclass(frozen=True)
class DemoResult:
    report: dict[str, Any]
    dashboard_key_secret: Optional[str]
    report_path: Optional[Path]


def _health(base_url: str, timeout_s: float = 10.0) -> None:
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=timeout_s) as response:
            body = response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise DemoError(f"cannot reach {base_url}/health: {exc}") from exc
    head = body.lstrip()[:64].lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
        raise DemoError(
            f"{base_url} returned HTML: this is the static dashboard, not the backend origin"
        )
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise DemoError(f"{base_url}/health did not return JSON") from exc
    if payload != {"status": "ok"}:
        raise DemoError(f"unexpected /health payload: {payload!r}")


def _preflight(base_url: str, origin: str, timeout_s: float = 10.0) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{base_url}{LOGIN_PROBE_PATH}",
        method="OPTIONS",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            status = response.status
            allowed = response.headers.get("access-control-allow-origin")
    except urllib.error.HTTPError as exc:
        status = exc.code
        allowed = (exc.headers or {}).get("access-control-allow-origin")
    return {"url": origin, "cors_ok": status == 200 and allowed == origin, "allowed_origin": allowed}


def _issue_dashboard_key(agent: SimulatedPaymentAgent, env_id: str) -> tuple[dict[str, Any], str]:
    suffix = uuid.uuid4().hex[:6]
    record = agent._data(
        "POST",
        "/v1/api-keys",
        {
            "env_id": env_id,
            "name": f"northstar-dashboard-{suffix}",
            "scope": "read",
            "role": "read_only",
        },
    )
    secret = record["secret"]
    public = {
        "id": record["id"],
        "name": record["name"],
        "prefix": record.get("secret_prefix"),
        "role": record.get("role"),
        "scope": record.get("scope"),
        "env_id": env_id,
    }
    return public, secret


def _verify_dashboard_key(base_url: str, secret: str, env_id: str) -> None:
    transport = SyncHTTPTransport(base_url, api_key=secret, retry=RetryPolicy(max_attempts=1))
    response = transport.request("GET", f"{LOGIN_PROBE_PATH}?env_id={env_id}&limit=1")
    payload = response.json() if response.body else {}
    if response.status != 200 or not payload.get("success"):
        raise DemoError(
            f"issued dashboard key failed the login probe: HTTP {response.status} {payload!r}"
        )


def run_demo(
    base_url: str,
    api_key: str,
    *,
    dashboard_url: Optional[str] = None,
    issue_dashboard_key: bool = True,
    report_path: Optional[Path] = DEFAULT_REPORT,
) -> DemoResult:
    base_url = base_url.rstrip("/")
    _health(base_url)

    agent = SimulatedPaymentAgent(base_url, api_key)
    try:
        report = agent.run()
    except ControlPlaneError as exc:
        raise DemoError(str(exc)) from exc

    env_id = report["environment"]["id"]
    secret: Optional[str] = None
    if issue_dashboard_key:
        try:
            public, secret = _issue_dashboard_key(agent, env_id)
        except ControlPlaneError as exc:
            raise DemoError(f"could not issue a dashboard key (admin key required): {exc}") from exc
        _verify_dashboard_key(base_url, secret, env_id)
        report["dashboard_key"] = public

    if dashboard_url:
        report["dashboard"] = _preflight(base_url, dashboard_url.rstrip("/"))

    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["scenario"] = [
        {
            "key": spec["key"],
            "merchant": spec["merchant"],
            "amount": spec["amount"],
            "expected_verdict": spec["expected_verdict"],
            "expected_state": spec["expected_state"],
        }
        for spec in TRANSACTIONS
    ]

    if report_path is not None:
        report_path = Path(report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n")

    return DemoResult(report=report, dashboard_key_secret=secret, report_path=report_path)


def _print_summary(result: DemoResult, base_url: str, dashboard_url: Optional[str]) -> None:
    report = result.report
    print("Northstar demo complete")
    print(f"  backend:      {base_url}")
    print(f"  organization: {report['org']['name']}  ({report['org']['id']})")
    print(
        f"  environment:  {report['environment']['name']} / {report['environment']['kind']}"
        f"  ({report['environment']['id']})"
    )
    print(f"  agent:        {report['agent']['name']}  ({report['agent']['id']})")
    print(f"  policy:       {report['policy']['name']} v{report['policy']['version']}")
    print()
    print(f"  {'merchant':<14}{'amount':>8}  {'verdict':<13}{'state':<13}payment")
    for row in report["transactions"]:
        print(
            f"  {row['merchant']:<14}{row['amount']:>8.2f}  {row['verdict']:<13}"
            f"{row['state']:<13}{row['payment_id']}"
        )
    if "dashboard" in report:
        status = "allowed" if report["dashboard"]["cors_ok"] else "BLOCKED"
        print()
        print(f"  CORS preflight from {report['dashboard']['url']}: {status}")
        if not report["dashboard"]["cors_ok"]:
            print("  -> set PAIZIQ_CORS_ORIGINS on the backend to include that origin and restart")
    if result.report_path is not None:
        print(f"\n  report: {result.report_path}")
    print()
    print("Next steps")
    login = f"{dashboard_url.rstrip('/')}/login" if dashboard_url else "<dashboard>/login"
    print(f"  1. Open {login}")
    print(f"  2. Backend URL: {base_url}")
    if result.dashboard_key_secret:
        print(f"  3. API key (read-only, shown once): {result.dashboard_key_secret}")
    else:
        print("  3. API key: a read-capable key for this backend")
    print(
        f"  4. Select organization '{report['org']['name']}' and environment "
        f"'{report['environment']['name']}'; keep the time range at Last 24 hours"
    )
    print("  5. Payment Feed shows the three rows above; open the cloudhost inc payment")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Northstar demo against a Paiziq backend")
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("PAIZIQ_ENDPOINT", "http://127.0.0.1:8800"),
        help="backend base URL (env PAIZIQ_ENDPOINT)",
    )
    parser.add_argument(
        "--dashboard-url",
        default=os.environ.get("PAIZIQ_DASHBOARD_URL") or None,
        help="deployed dashboard origin for the CORS check and next steps",
    )
    parser.add_argument(
        "--out",
        default=os.environ.get("PAIZIQ_DEMO_REPORT", str(DEFAULT_REPORT)),
        help="where to write the JSON report (no secrets)",
    )
    parser.add_argument(
        "--no-dashboard-key",
        action="store_true",
        help="do not issue a read-only dashboard key",
    )
    args = parser.parse_args(argv)

    api_key = os.environ.get("PAIZIQ_API_KEY", "")
    if not api_key:
        print("PAIZIQ_API_KEY is required (never pass secrets as CLI arguments)", file=sys.stderr)
        return 2

    try:
        result = run_demo(
            args.endpoint,
            api_key,
            dashboard_url=args.dashboard_url,
            issue_dashboard_key=not args.no_dashboard_key,
            report_path=Path(args.out),
        )
    except DemoError as exc:
        print(f"Northstar demo failed: {exc}", file=sys.stderr)
        return 1
    _print_summary(result, args.endpoint.rstrip("/"), args.dashboard_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
