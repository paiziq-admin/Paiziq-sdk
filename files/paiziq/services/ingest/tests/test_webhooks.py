"""Webhook delivery tests (PZ-076/PZ-077/PZ-078)."""

from __future__ import annotations

import json
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app, store  # noqa: E402
from webhook_worker import process_due_deliveries  # noqa: E402

ADMIN = {"Authorization": "Bearer dev-key"}
client = TestClient(app)


def _name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _env() -> dict:
    org = client.post("/v1/orgs", json={"name": _name("org")}, headers=ADMIN).json()["data"]
    return client.post(
        f"/v1/orgs/{org['id']}/environments",
        json={"name": "sandbox", "kind": "sandbox"}, headers=ADMIN,
    ).json()["data"]


def test_create_webhook_endpoint_returns_secret_once():
    env = _env()
    r = client.post(
        "/v1/webhook-endpoints",
        json={"env_id": env["id"], "url": "https://example.com/hook", "events": ["*"]},
        headers=ADMIN,
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["secret"].startswith("whsec_")
    listed = client.get(f"/v1/webhook-endpoints?env_id={env['id']}", headers=ADMIN).json()["data"]
    assert "secret" not in listed[0]


def test_worker_delivers_signed_payload():
    env = _env()
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            received["signature"] = self.headers.get("Paiziq-Signature")
            received["body"] = body
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    port = server.server_address[1]

    created = client.post(
        "/v1/webhook-endpoints",
        json={"env_id": env["id"], "url": f"http://127.0.0.1:{port}/", "events": ["decision.created"]},
        headers=ADMIN,
    ).json()["data"]
    endpoint_id = created["id"]
    secret = created["secret"]

    store.connection.execute(
        "INSERT INTO webhook_deliveries (id, endpoint_id, event_type, payload, state, attempts, "
        "next_attempt_ms, created_at_ms, updated_at_ms) VALUES (?, ?, ?, ?, 'pending', 0, 0, 0, 0)",
        ("whd_test", endpoint_id, "decision.created", json.dumps({"type": "decision.created", "data": {}})),
    )
    store.connection.commit()

    import asyncio
    from deps import get_webhook_store
    asyncio.run(process_due_deliveries(get_webhook_store()))

    assert received["signature"] is not None
    from paiziq.webhooks import verify_webhook_signature
    assert verify_webhook_signature(received["body"], received["signature"], secret)


def test_delivery_list_filters_by_environment_and_payment():
    env = _env()
    endpoint = client.post(
        "/v1/webhook-endpoints",
        json={
            "env_id": env["id"],
            "url": "https://example.com/correlation",
            "events": ["payment.updated"],
        },
        headers=ADMIN,
    ).json()["data"]
    delivery_id = f"whd_{uuid.uuid4().hex}"
    store.connection.execute(
        "INSERT INTO webhook_deliveries "
        "(id, endpoint_id, event_type, payload, state, attempts, "
        "next_attempt_ms, created_at_ms, updated_at_ms) "
        "VALUES (?, ?, 'payment.updated', ?, 'pending', 0, 0, 1, 1)",
        (
            delivery_id,
            endpoint["id"],
            json.dumps(
                {
                    "type": "payment.updated",
                    "data": {"payment_id": "pay_correlated"},
                }
            ),
        ),
    )
    store.connection.commit()

    matched = client.get(
        "/v1/webhook-deliveries"
        f"?env_id={env['id']}&payment_id=pay_correlated",
        headers=ADMIN,
    ).json()
    assert matched["meta"]["total"] == 1
    assert matched["data"][0]["id"] == delivery_id

    missing = client.get(
        "/v1/webhook-deliveries?payment_id=pay_other",
        headers=ADMIN,
    ).json()
    assert missing["meta"]["total"] == 0
