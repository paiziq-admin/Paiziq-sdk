"""Ingest service tests: auth, idempotent span upsert, size limits,
notification intake, and the SDK wire-contract roundtrip."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app  # noqa: E402

AUTH = {"Authorization": "Bearer dev-key"}
client = TestClient(app)


def span(span_id: str = "s1", trace_id: str = "tr1", **kw) -> dict:
    base = {
        "name": "paiziq.review_payment",
        "trace_id": trace_id,
        "span_id": span_id,
        "start_ms": 1,
        "end_ms": 2,
        "status": "ok",
        "attributes": {"paiziq.decision": "approved"},
        "events": [],
    }
    base.update(kw)
    return base


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_traces_require_api_key():
    assert client.post("/v1/traces", json={"spans": []}).status_code == 401


def test_traces_reject_bad_key():
    r = client.post(
        "/v1/traces", json={"spans": []}, headers={"Authorization": "Bearer wrong"}
    )
    assert r.status_code == 403


def test_span_upsert_is_idempotent():
    batch = {"spans": [span("dup-1", "tr-dup")]}
    assert client.post("/v1/traces", json=batch, headers=AUTH).status_code == 200
    assert client.post("/v1/traces", json=batch, headers=AUTH).status_code == 200
    spans = client.get("/v1/traces/tr-dup", headers=AUTH).json()["spans"]
    assert len(spans) == 1  # redelivery did not duplicate


def test_upsert_updates_span_payload():
    client.post("/v1/traces", json={"spans": [span("u-1", "tr-u", status="ok")]}, headers=AUTH)
    client.post("/v1/traces", json={"spans": [span("u-1", "tr-u", status="error")]}, headers=AUTH)
    spans = client.get("/v1/traces/tr-u", headers=AUTH).json()["spans"]
    assert len(spans) == 1
    assert spans[0]["status"] == "error"


def test_batch_span_limit():
    big = {"spans": [span(f"b-{i}", "tr-big") for i in range(501)]}
    assert client.post("/v1/traces", json=big, headers=AUTH).status_code == 413


def test_body_size_limit():
    r = client.post(
        "/v1/traces", json={"spans": []},
        headers={**AUTH, "Content-Length": "2000000"},
    )
    assert r.status_code == 413


def test_notifications_roundtrip():
    body = {
        "severity": "critical",
        "title": "Harmful intent suspected",
        "message": "agent-1 attempted a blocked merchant",
        "request_id": "pay_123",
        "risk_flags": ["harmful_intent_suspected"],
        "created_at_ms": 1700000000000,
    }
    assert client.post("/v1/notifications", json=body, headers=AUTH).status_code == 200
    items = client.get("/v1/notifications", headers=AUTH).json()["notifications"]
    assert items[0]["title"] == "Harmful intent suspected"
    assert items[0]["risk_flags"] == ["harmful_intent_suspected"]


def test_sdk_wire_contract_roundtrip():
    """A span dict produced by the SDK's Span.to_dict() is accepted as-is."""
    sdk_src = Path(__file__).resolve().parents[3] / "sdk" / "src"
    sys.path.insert(0, str(sdk_src))
    from paiziq.tracing.tracer import Span  # noqa: E402

    s = Span(name="paiziq.execute_payment", trace_id="tr-sdk")
    s.add_event("four_way_audit", {"checks": []})
    s.end("ok")
    r = client.post("/v1/traces", json={"spans": [s.to_dict()]}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["accepted"] == 1
    spans = client.get("/v1/traces/tr-sdk", headers=AUTH).json()["spans"]
    assert spans[0]["events"][0]["name"] == "four_way_audit"
