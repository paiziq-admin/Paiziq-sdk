"""SDK-to-backend integration tests (PZ-043).

The real SDK talks to the real ingest service over HTTP: uvicorn hosts
the FastAPI app on an ephemeral localhost port, and the SDK's
exporter, webhook notifier, and sync transport drive it end to end.
Offline-safe: everything stays on 127.0.0.1.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app, store  # noqa: E402

from paiziq import PaiziqSDK, PaymentPolicy, PaymentRequest  # noqa: E402
from paiziq.notifications import WebhookNotifier  # noqa: E402
from paiziq.tracing.tracer import HTTPExporter  # noqa: E402
from paiziq.transport import RetryPolicy, SyncHTTPTransport  # noqa: E402


@pytest.fixture(scope="module")
def base_url():
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="itest-uvicorn", daemon=True)
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


def _transport(base_url: str) -> SyncHTTPTransport:
    return SyncHTTPTransport(base_url, api_key="dev-key", retry=RetryPolicy(max_attempts=2))


def _wait_for(predicate, timeout_s: float = 5.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    return predicate()


def test_sdk_trace_export_reaches_backend(base_url):
    request_id = f"int-req-{uuid.uuid4().hex[:8]}"
    exporter = HTTPExporter(base_url, "dev-key", batch_size=10, flush_interval_s=0.1)
    sdk = PaiziqSDK(exporters=[exporter], notifiers=[], service_name="integration-agent")
    decision = sdk.review_payment(
        PaymentRequest(
            agent_id="int-agent", principal_id="u1", merchant="acme corp",
            amount=10.0, currency="USD", intent_description="integration trace",
            request_id=request_id,
        )
    )
    assert decision.status.value == "approved"
    exporter.shutdown()  # drain the batch queue

    row = _wait_for(
        lambda: store.connection.execute(
            "SELECT trace_id FROM spans WHERE payload LIKE ?", (f"%{request_id}%",)
        ).fetchone()
    )
    assert row is not None, "exported span never reached the backend"

    # read back over the wire, exactly as the dashboard would
    body = _transport(base_url).get(f"/v1/traces/{row[0]}").json()
    names = [s["name"] for s in body["spans"]]
    assert "paiziq.review_payment" in names
    span = next(s for s in body["spans"] if s["name"] == "paiziq.review_payment")
    assert span["attributes"]["paiziq.request_id"] == request_id


def test_sdk_webhook_notifier_delivers_to_backend(base_url):
    request_id = f"int-req-{uuid.uuid4().hex[:8]}"
    notifier = WebhookNotifier(f"{base_url}/v1/notifications", api_key="dev-key")
    sdk = PaiziqSDK(
        exporters=[], notifiers=[notifier],
        policy=PaymentPolicy(review_threshold=10.0, hard_limit=1000.0),
        service_name="integration-agent",
    )
    decision = sdk.review_payment(
        PaymentRequest(
            agent_id="int-agent", principal_id="u1", merchant="acme corp",
            amount=500.0, currency="USD", intent_description="needs review",
            request_id=request_id,
        )
    )
    assert decision.status.value == "needs_review"

    notifications = _wait_for(
        lambda: [
            n for n in json.loads(
                _transport(base_url).get("/v1/notifications").body.decode()
            )["notifications"]
            if n["request_id"] == request_id
        ]
    )
    assert notifications, "webhook notification never arrived"
    assert "review" in notifications[0]["title"].lower()


def test_control_plane_roundtrip_via_sdk_transport(base_url):
    t = _transport(base_url)
    org = t.post("/v1/orgs", {"name": f"int-{uuid.uuid4().hex[:8]}"}).json()["data"]
    env = t.post(
        f"/v1/orgs/{org['id']}/environments", {"name": "sandbox", "kind": "sandbox"}
    ).json()["data"]
    agent = t.post(
        "/v1/agents", {"env_id": env["id"], "name": "integration-agent"}
    ).json()["data"]
    payment = t.post(
        "/v1/payments",
        {"env_id": env["id"], "agent_id": agent["id"], "principal_id": "u1",
         "merchant": "acme corp", "amount": 42.0,
         "intent_description": "integration payment"},
    ).json()["data"]
    decision = t.post("/v1/decisions", {"payment_id": payment["id"]}).json()["data"]
    assert decision["verdict"] == "approved"

    detail = t.get(f"/v1/payments/{payment['id']}").json()["data"]
    assert detail["state"] == "approved"


def test_transport_surfaces_auth_failures(base_url):
    bad = SyncHTTPTransport(base_url, api_key="wrong-key",
                            retry=RetryPolicy(max_attempts=1))
    response = bad.get("/v1/notifications")
    assert response.status == 403
