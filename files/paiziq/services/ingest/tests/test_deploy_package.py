"""Deployment package lane: container recipe, entrypoint, smoke test, Northstar demo.

# AC: The backend ships as a runnable package. The Dockerfile and entrypoint
# start the service in production mode with a persistent SQLite path, the
# smoke test mirrors the dashboard login probe, and the Northstar demo runs
# the real SDK scenario against a live backend and issues a read-only key.
# Behavior: entrypoint subprocess -> /health + DB file; run_smoke -> pass /
# fail; run_demo -> three verdicts, scoped key, secret-free report.
# @category: integration
# @lane: integration
# @dependency: uvicorn, paiziq SDK, ingest API, SQLite
# @complexity: medium
# ROI: 90

Runs under make ingest-test. Docker itself is exercised by make docker-smoke.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import uvicorn

_INGEST = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
_PROJECT = _INGEST.parents[1]
for entry in (_INGEST, _TESTS, _INGEST / "scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from app import app  # noqa: E402
from e2e_support.scenario import TRANSACTIONS  # noqa: E402
from paiziq.transport import RetryPolicy, SyncHTTPTransport  # noqa: E402

import northstar_demo  # noqa: E402
import smoke_backend  # noqa: E402

DOCKERFILE = _INGEST / "Dockerfile"
ENTRYPOINT = _INGEST / "entrypoint.sh"
DOCKERIGNORE = _PROJECT / ".dockerignore"
DEPLOY_SCRIPT = _PROJECT / "deploy" / "azure" / "deploy_backend.sh"
ENV_EXAMPLE = _PROJECT / "deploy" / "azure" / "backend.env.example"
DASHBOARD_ORIGIN = "http://127.0.0.1:4173"


# --- static packaging checks ---------------------------------------------------


def test_dockerfile_builds_a_production_ready_image():
    text = DOCKERFILE.read_text()
    assert "COPY sdk/src sdk/src" in text, "the SDK must be installed from the local source tree"
    assert "pip3 install" in text
    assert "USER paiziq" in text, "the service must not run as root"
    assert "PAIZIQ_INGEST_DB=/data/paiziq.sqlite" in text
    assert 'VOLUME ["/data"]' in text
    assert "EXPOSE 8800" in text
    assert "HEALTHCHECK" in text
    assert 'ENTRYPOINT ["/srv/services/ingest/entrypoint.sh"]' in text
    assert "dev-key" not in text


def test_entrypoint_runs_a_single_uvicorn_worker():
    text = ENTRYPOINT.read_text()
    assert text.startswith("#!/usr/bin/env sh") or text.startswith("#!/bin/sh")
    assert "--workers 1" in text, "SQLite + in-process webhook worker require one process"
    assert "exec python3 -m uvicorn app:app" in text
    assert os.access(ENTRYPOINT, os.X_OK)


def test_dockerignore_excludes_tests_secrets_and_databases():
    lines = {line.strip() for line in DOCKERIGNORE.read_text().splitlines() if line.strip()}
    for required in ("services/ingest/tests", "sdk/tests", "**/.venv", "*.sqlite", ".env", ".env.*"):
        assert required in lines, f"{required} missing from .dockerignore"


def test_deploy_script_and_env_example_are_consistent():
    script = DEPLOY_SCRIPT.read_text()
    example = ENV_EXAMPLE.read_text()
    assert os.access(DEPLOY_SCRIPT, os.X_OK)
    assert "set -euo pipefail" in script
    assert "nobrl" in script, "Azure Files must be mounted with nobrl for SQLite"
    assert "--min-replicas 1 --max-replicas 1" in script
    assert "PAIZIQ_ENV=production" in script
    assert "secretref:ingest-keys" in script, "the bootstrap key must be a Container Apps secret"
    assert "dev-key" in script and "fail" in script, "dev-key must be rejected"
    for var in ("AZ_ACR_NAME", "AZ_STORAGE_ACCOUNT", "PAIZIQ_INGEST_KEYS", "PAIZIQ_CORS_ORIGINS"):
        assert var in script and var in example
    assert "PAIZIQ_INGEST_KEYS=\n" in example, "the example must not ship a key value"
    completed = subprocess.run(["bash", "-n", str(DEPLOY_SCRIPT)], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


# --- entrypoint subprocess ------------------------------------------------------


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_health(base_url: str, timeout_s: float = 20.0) -> dict:
    deadline = time.time() + timeout_s
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=2) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:  # noqa: BLE001 - startup polling
            last_error = exc
            time.sleep(0.2)
    raise AssertionError(f"service did not become healthy: {last_error}")


def test_entrypoint_starts_production_service_and_creates_db(tmp_path):
    port = _free_port()
    db_path = tmp_path / "nested" / "dir" / "paiziq.sqlite"
    key = "pzq_test_" + secrets.token_urlsafe(24)
    env = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}",
        "PAIZIQ_ENV": "production",
        "PAIZIQ_INGEST_KEYS": key,
        "PAIZIQ_INGEST_DB": str(db_path),
        "PAIZIQ_CORS_ORIGINS": DASHBOARD_ORIGIN,
        "PORT": str(port),
    }
    proc = subprocess.Popen(
        [str(ENTRYPOINT)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    try:
        base_url = f"http://127.0.0.1:{port}"
        assert _wait_health(base_url) == {"status": "ok"}
        assert db_path.exists(), "entrypoint must create the DB parent directory"
        results = smoke_backend.run_smoke(base_url, key, origin=DASHBOARD_ORIGIN)
        assert all(r.passed for r in results), [r for r in results if not r.passed]
        assert [r.name for r in results] == [
            "health",
            "login_probe",
            "wrong_key_rejected",
            "missing_key_rejected",
            "cors_preflight",
        ]
        # The demo's CORS preflight is only meaningful against a configured runtime.
        result = northstar_demo.run_demo(
            base_url,
            key,
            dashboard_url=DASHBOARD_ORIGIN,
            issue_dashboard_key=False,
            report_path=tmp_path / "northstar.json",
        )
        assert result.dashboard_key_secret is None
        assert result.report["dashboard"] == {
            "url": DASHBOARD_ORIGIN,
            "cors_ok": True,
            "allowed_origin": DASHBOARD_ORIGIN,
        }
        assert "dashboard_key" not in result.report
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


# --- in-process backend for smoke and demo ------------------------------------


@pytest.fixture(scope="module")
def base_url():
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="deploy-uvicorn", daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            pytest.fail("uvicorn did not start within 10s")
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def test_smoke_passes_with_valid_key(base_url):
    results = smoke_backend.run_smoke(base_url, "dev-key")
    assert [r.name for r in results] == [
        "health",
        "login_probe",
        "wrong_key_rejected",
        "missing_key_rejected",
    ]
    assert all(r.passed for r in results), [r for r in results if not r.passed]


def test_smoke_fails_login_probe_with_wrong_key(base_url):
    results = {r.name: r for r in smoke_backend.run_smoke(base_url, "pzq_wrong_" + "x" * 24)}
    assert results["health"].passed
    assert not results["login_probe"].passed
    assert "403" in results["login_probe"].detail


def test_smoke_flags_origin_the_backend_does_not_allow(base_url):
    # The in-process app has no PAIZIQ_CORS_ORIGINS, so any origin must be flagged.
    checks = smoke_backend.run_smoke(base_url, "dev-key", origin=DASHBOARD_ORIGIN)
    results = {r.name: r for r in checks}
    assert not results["cors_preflight"].passed
    assert "PAIZIQ_CORS_ORIGINS" in results["cors_preflight"].detail


def test_smoke_cli_requires_api_key_env(monkeypatch, capsys):
    monkeypatch.delenv("PAIZIQ_API_KEY", raising=False)
    assert smoke_backend.main(["--endpoint", "http://127.0.0.1:1"]) == 2
    assert "PAIZIQ_API_KEY" in capsys.readouterr().err


@pytest.fixture(scope="module")
def demo(base_url, tmp_path_factory):
    report_path = tmp_path_factory.mktemp("northstar") / "run.json"
    return northstar_demo.run_demo(
        base_url, "dev-key", dashboard_url=DASHBOARD_ORIGIN, report_path=report_path
    )


def test_demo_runs_the_scenario_with_expected_verdicts(demo):
    rows = {row["merchant"]: row for row in demo.report["transactions"]}
    expected = {item["merchant"]: item for item in TRANSACTIONS}
    assert set(rows) == set(expected)
    for merchant, item in expected.items():
        assert rows[merchant]["verdict"] == item["expected_verdict"], merchant
        assert rows[merchant]["payment_id"].startswith("pay_")
    # No CORS configured in-process: the demo must report it rather than hide it.
    assert demo.report["dashboard"]["cors_ok"] is False
    assert [(row["key"], row["merchant"], row["expected_verdict"]) for row in demo.report["scenario"]] == [
        (item["key"], item["merchant"], item["expected_verdict"]) for item in TRANSACTIONS
    ]


def test_demo_issues_a_read_only_dashboard_key(demo, base_url):
    assert demo.dashboard_key_secret
    assert demo.report["dashboard_key"]["role"] == "read_only"
    assert demo.report["dashboard_key"]["scope"] == "read"
    transport = SyncHTTPTransport(
        base_url, api_key=demo.dashboard_key_secret, retry=RetryPolicy(max_attempts=1)
    )
    env_id = demo.report["environment"]["id"]
    listing = transport.request("GET", f"/v1/agents?env_id={env_id}&limit=1")
    assert listing.status == 200
    assert [item["id"] for item in listing.json()["data"]] == [demo.report["agent"]["id"]]
    # A dashboard key must not be able to mint further keys.
    forbidden = transport.request(
        "POST",
        "/v1/api-keys",
        json_body={"env_id": env_id, "name": "escalation", "scope": "admin"},
    )
    assert forbidden.status == 403


def test_demo_report_contains_no_secrets(demo):
    assert demo.report_path is not None and demo.report_path.exists()
    text = demo.report_path.read_text()
    assert demo.dashboard_key_secret not in text
    assert "dev-key" not in text
    assert "secret" not in json.dumps(json.loads(text)).lower()


def test_demo_cli_requires_api_key_env(monkeypatch, capsys):
    monkeypatch.delenv("PAIZIQ_API_KEY", raising=False)
    assert northstar_demo.main(["--endpoint", "http://127.0.0.1:1"]) == 2
    assert "PAIZIQ_API_KEY" in capsys.readouterr().err


def test_demo_rejects_static_site_answering_health(tmp_path):
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Html(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = b"<!doctype html><html><body>dashboard</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    httpd = HTTPServer(("127.0.0.1", 0), Html)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{httpd.server_port}"
        with pytest.raises(northstar_demo.DemoError):
            northstar_demo.run_demo(url, "dev-key", report_path=tmp_path / "x.json")
        health = smoke_backend.run_smoke(url, "dev-key")[0]
        assert not health.passed
    finally:
        httpd.shutdown()
