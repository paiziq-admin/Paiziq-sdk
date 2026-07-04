"""Decision service-boundary tests (contract §8): SDK engine verdicts
persist as immutable decisions, drive payment state transitions, and
open reviews on needs_review."""

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


def _payment(amount: float, currency: str = "USD") -> dict:
    org = client.post("/v1/orgs", json={"name": _name("org")}, headers=AUTH).json()["data"]
    env = client.post(
        f"/v1/orgs/{org['id']}/environments",
        json={"name": "sandbox", "kind": "sandbox"}, headers=AUTH,
    ).json()["data"]
    agent = client.post(
        "/v1/agents", json={"env_id": env["id"], "name": _name("agent")}, headers=AUTH
    ).json()["data"]
    r = client.post(
        "/v1/payments",
        json={"env_id": env["id"], "agent_id": agent["id"], "principal_id": "user-42",
              "merchant": "acme corp", "amount": amount, "currency": currency,
              "intent_description": "Renew subscription"},
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _evaluate(payment_id: str):
    return client.post("/v1/decisions", json={"payment_id": payment_id}, headers=AUTH)


def test_small_amount_is_approved_and_transitions_payment():
    payment = _payment(49.99)
    r = _evaluate(payment["id"])
    assert r.status_code == 200, r.text
    decision = r.json()["data"]
    assert decision["id"].startswith("dec_")
    assert decision["verdict"] == "approved"
    assert decision["reasons"] == ["All decision rules passed"]
    assert decision["review_id"] is None
    assert decision["policy_version"] is None  # no policy published in this env

    detail = client.get(f"/v1/payments/{payment['id']}", headers=AUTH).json()["data"]
    assert detail["state"] == "approved"
    assert [(t["from"], t["to"]) for t in detail["transitions"]] == [("proposed", "approved")]
    assert detail["transitions"][0]["reason"] == f"decision {decision['id']}"


def test_over_review_threshold_opens_review():
    payment = _payment(500.0)  # default review threshold is 100
    decision = _evaluate(payment["id"]).json()["data"]
    assert decision["verdict"] == "needs_review"
    assert "over_review_threshold" in decision["risk_flags"]
    assert decision["review_id"].startswith("rev_")

    review = store.connection.execute(
        "SELECT payment_id, decision_id, state FROM reviews WHERE id = ?",
        (decision["review_id"],),
    ).fetchone()
    assert review == (payment["id"], decision["id"], "open")

    payment_state = client.get(
        f"/v1/payments/{payment['id']}", headers=AUTH
    ).json()["data"]["state"]
    assert payment_state == "needs_review"


def test_over_hard_limit_is_rejected():
    payment = _payment(5000.0)  # default hard limit is 1000
    decision = _evaluate(payment["id"]).json()["data"]
    assert decision["verdict"] == "rejected"
    assert "over_hard_limit" in decision["risk_flags"]
    payment_state = client.get(
        f"/v1/payments/{payment['id']}", headers=AUTH
    ).json()["data"]["state"]
    assert payment_state == "rejected"


def test_disallowed_currency_needs_review():
    payment = _payment(20.0, currency="EUR")  # default policy allows USD only
    decision = _evaluate(payment["id"]).json()["data"]
    assert decision["verdict"] == "needs_review"
    assert "currency_not_permitted" in decision["risk_flags"]


def test_unknown_payment_404():
    assert _evaluate("pay_missing").status_code == 404


def test_terminal_payment_cannot_be_evaluated():
    payment = _payment(49.99)
    _evaluate(payment["id"])  # proposed -> approved
    client.post(
        f"/v1/payments/{payment['id']}/transition", json={"to": "executed"}, headers=AUTH
    )
    r = _evaluate(payment["id"])
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "invalid_state_transition"


def test_reevaluation_appends_new_immutable_decision():
    payment = _payment(500.0)
    first = _evaluate(payment["id"]).json()["data"]
    second = _evaluate(payment["id"]).json()["data"]  # needs_review is evaluable
    assert first["id"] != second["id"]

    listed = client.get(f"/v1/decisions?payment_id={payment['id']}", headers=AUTH).json()
    assert listed["meta"]["total"] == 2
    assert [d["id"] for d in listed["data"]] == [first["id"], second["id"]]


def test_get_decision_roundtrip_and_404():
    payment = _payment(49.99)
    decision = _evaluate(payment["id"]).json()["data"]
    fetched = client.get(f"/v1/decisions/{decision['id']}", headers=AUTH).json()["data"]
    assert fetched["id"] == decision["id"]
    assert fetched["verdict"] == "approved"
    assert client.get("/v1/decisions/dec_missing", headers=AUTH).status_code == 404


def test_decision_writes_audit_log():
    payment = _payment(49.99)
    decision = _evaluate(payment["id"]).json()["data"]
    rows = store.connection.execute(
        "SELECT action FROM audit_log WHERE resource = ? ORDER BY id", (decision["id"],)
    ).fetchall()
    assert [r[0] for r in rows] == ["decision.create"]
