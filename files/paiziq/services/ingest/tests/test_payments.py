"""Payment API tests (contract §7): creation with tenancy checks,
Idempotency-Key replay, state-machine enforcement, append-only
transition history, filters, and audit side effects."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app, store  # noqa: E402

AUTH = {"Authorization": "Bearer dev-key"}
client = TestClient(app)


def _name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _agent_env() -> tuple[dict, dict]:
    org = client.post("/v1/orgs", json={"name": _name("org")}, headers=AUTH).json()["data"]
    env = client.post(
        f"/v1/orgs/{org['id']}/environments",
        json={"name": "sandbox", "kind": "sandbox"}, headers=AUTH,
    ).json()["data"]
    agent = client.post(
        "/v1/agents", json={"env_id": env["id"], "name": _name("agent")}, headers=AUTH
    ).json()["data"]
    return env, agent


def _propose(env: dict, agent: dict, headers: dict | None = None, **overrides) -> dict:
    body = {
        "env_id": env["id"], "agent_id": agent["id"], "principal_id": "user-42",
        "merchant": "acme corp", "amount": 49.99, "currency": "usd",
        "intent_description": "Renew subscription",
        **overrides,
    }
    r = client.post("/v1/payments", json=body, headers={**AUTH, **(headers or {})})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _transition(payment_id: str, to: str, reason: str | None = None):
    return client.post(
        f"/v1/payments/{payment_id}/transition",
        json={"to": to, "reason": reason}, headers=AUTH,
    )


def test_create_payment_proposal():
    env, agent = _agent_env()
    payment = _propose(env, agent)
    assert payment["id"].startswith("pay_")
    assert payment["state"] == "proposed"
    assert payment["currency"] == "USD"  # normalized to upper case
    assert payment["created_at_ms"] == payment["updated_at_ms"]


def test_create_payment_unknown_env_and_agent_404():
    env, agent = _agent_env()
    base = {"principal_id": "u", "merchant": "m", "amount": 1.0}
    r = client.post(
        "/v1/payments",
        json={"env_id": "env_missing", "agent_id": agent["id"], **base}, headers=AUTH,
    )
    assert r.status_code == 404
    r = client.post(
        "/v1/payments",
        json={"env_id": env["id"], "agent_id": "agt_missing", **base}, headers=AUTH,
    )
    assert r.status_code == 404


def test_create_payment_agent_env_mismatch_422():
    env_a, _ = _agent_env()
    _, agent_b = _agent_env()
    r = client.post(
        "/v1/payments",
        json={"env_id": env_a["id"], "agent_id": agent_b["id"],
              "principal_id": "u", "merchant": "m", "amount": 1.0},
        headers=AUTH,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_idempotency_key_replay_returns_original():
    env, agent = _agent_env()
    key = {"Idempotency-Key": f"idem-{uuid.uuid4().hex}"}
    first = _propose(env, agent, headers=key)
    second = _propose(env, agent, headers=key, amount=999.0)  # replay ignores new body
    assert second == first
    listed = client.get(f"/v1/payments?env_id={env['id']}", headers=AUTH).json()
    assert listed["meta"]["total"] == 1


def test_happy_path_transitions_with_history():
    env, agent = _agent_env()
    payment = _propose(env, agent)
    assert _transition(payment["id"], "approved", "within limits").status_code == 200
    r = _transition(payment["id"], "executed")
    assert r.status_code == 200
    assert r.json()["data"]["state"] == "executed"

    detail = client.get(f"/v1/payments/{payment['id']}", headers=AUTH).json()["data"]
    history = [(t["from"], t["to"]) for t in detail["transitions"]]
    assert history == [("proposed", "approved"), ("approved", "executed")]
    assert detail["transitions"][0]["reason"] == "within limits"
    assert detail["updated_at_ms"] >= detail["created_at_ms"]


def test_review_path_and_terminal_states():
    env, agent = _agent_env()
    payment = _propose(env, agent)
    assert _transition(payment["id"], "needs_review").status_code == 200
    assert _transition(payment["id"], "rejected").status_code == 200
    # rejected is terminal: nothing may leave it
    for to in ("approved", "executed", "needs_review"):
        r = _transition(payment["id"], to)
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "invalid_state_transition"


def test_invalid_transitions_from_proposed():
    env, agent = _agent_env()
    payment = _propose(env, agent)
    for to in ("executed", "failed"):
        assert _transition(payment["id"], to).status_code == 409


def test_transition_unknown_payment_404():
    assert _transition("pay_missing", "approved").status_code == 404


def test_list_payments_filters():
    env, agent = _agent_env()
    p1 = _propose(env, agent)
    p2 = _propose(env, agent)
    _transition(p1["id"], "approved")

    by_state = client.get(
        f"/v1/payments?env_id={env['id']}&state=proposed", headers=AUTH
    ).json()
    assert [p["id"] for p in by_state["data"]] == [p2["id"]]

    by_agent = client.get(f"/v1/payments?agent_id={agent['id']}", headers=AUTH).json()
    assert by_agent["meta"]["total"] == 2


def test_payment_mutations_write_audit_log():
    env, agent = _agent_env()
    payment = _propose(env, agent)
    _transition(payment["id"], "approved")
    rows = store.connection.execute(
        "SELECT action FROM audit_log WHERE resource = ? ORDER BY id", (payment["id"],)
    ).fetchall()
    assert [r[0] for r in rows] == ["payment.create", "payment.transition"]
