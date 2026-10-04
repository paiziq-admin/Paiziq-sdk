"""Execution boundary regression tests use synthetic receipts; never live money."""

from concurrent.futures import ThreadPoolExecutor
import json
from decimal import Decimal
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

from app import app, store
from deps import get_event_router, get_execution_store

client = TestClient(app)
AUTH = {"Authorization": "Bearer dev-key"}


def setup(document=None):
    org = client.post(
        "/v1/orgs", json={"name": "execution-" + uuid.uuid4().hex}, headers=AUTH
    ).json()["data"]
    env = client.post(
        f"/v1/orgs/{org['id']}/environments",
        json={"name": "sandbox", "kind": "sandbox"},
        headers=AUTH,
    ).json()["data"]
    agent = client.post(
        "/v1/agents", json={"env_id": env["id"], "name": "worker"}, headers=AUTH
    ).json()["data"]
    policy = client.post(
        "/v1/policies",
        json={
            "env_id": env["id"],
            "name": "limits",
            "document": document
            or {
                "daily_budget": 100,
                "review_threshold": 100,
                "hard_limit": 1000,
                "budget_warning_ratio": 1,
            },
        },
        headers=AUTH,
    ).json()["data"]
    client.post(f"/v1/policies/{policy['id']}/publish", headers=AUTH)
    return env, agent, policy


def propose(scope, amount=60, **kwargs):
    env, agent, _ = scope
    response = client.post(
        "/v1/payments",
        json={
            "env_id": env["id"],
            "agent_id": agent["id"],
            "principal_id": "principal",
            "merchant": "vendor",
            "amount": amount,
            **kwargs,
        },
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def claim(payment, headers=AUTH):
    return client.post(
        f"/v1/payments/{payment['id']}/execution/claim", json={}, headers=headers
    )


def report(payment, token, status, headers=AUTH, **kwargs):
    return client.post(
        f"/v1/payments/{payment['id']}/execution/report",
        json={"claim_token": token, "status": status, **kwargs},
        headers=headers,
    )


def evidence(payment):
    response = client.get(f"/v1/payments/{payment['id']}/execution", headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def confirm(payment):
    granted = claim(payment).json()["data"]
    token = granted["claim_token"]
    assert report(payment, token, "submitted").status_code == 200
    receipt = "test-receipt-" + uuid.uuid4().hex
    assert (
        report(
            payment,
            token,
            "confirmed",
            gateway_reference=receipt,
            evidence={"source": "test_provider", "receipt": receipt},
        ).status_code
        == 200
    )
    return token, receipt


def test_atomic_claims_and_duplicate_replay():
    scope = setup()
    first, second = propose(scope), propose(scope)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, [first, second]))
    assert all(r.status_code == 200 for r in results)
    assert sum(r.json()["data"]["acquired"] for r in results) == 1
    winner = next(
        payment
        for payment, response in zip([first, second], results)
        if response.json()["data"]["acquired"]
    )
    again = claim(winner).json()["data"]
    assert not again["acquired"] and again["claim_token"] is None
    data = evidence(winner)
    assert data["budget"]["reserved_amount"] == "60.0"
    assert data["reservation"]["status"] == "held"
    assert data["execution"]["status"] == "reserved"


def test_confirmed_spend_and_velocity_seen_by_hosted_decision():
    scope = setup(
        {
            "daily_budget": 100,
            "review_threshold": 100,
            "hard_limit": 1000,
            "budget_warning_ratio": 1,
            "max_tx_per_hour": 1,
        }
    )
    first = propose(scope)
    confirm(first)
    second = propose(scope)
    response = client.post(
        "/v1/decisions", json={"payment_id": second["id"]}, headers=AUTH
    )
    verdict = response.json()["data"]
    assert verdict["verdict"] == "rejected"
    assert {"budget_exceeded", "velocity_anomaly"} <= set(verdict["risk_flags"])
    assert evidence(first)["budget"]["committed_amount"] == "60.0"


def test_unknown_requires_admin_evidence_and_does_not_release_budget():
    scope = setup()
    first = propose(scope)
    token = claim(first).json()["data"]["claim_token"]
    assert report(first, token, "submitted").status_code == 200
    assert report(first, token, "unknown", error="provider timeout").status_code == 200
    assert not claim(propose(scope)).json()["data"]["acquired"]
    assert (
        report(first, token, "failed", evidence={"assertion": "no charge"}).status_code
        == 409
    )
    assert (
        client.post(
            f"/v1/payments/{first['id']}/transition",
            json={"to": "failed"},
            headers=AUTH,
        ).status_code
        == 409
    )
    missing = client.post(
        f"/v1/payments/{first['id']}/execution/reconcile",
        json={"status": "failed", "reason": "lookup", "evidence": {}},
        headers=AUTH,
    )
    assert missing.status_code == 422
    response = client.post(
        f"/v1/payments/{first['id']}/execution/reconcile",
        json={
            "status": "confirmed",
            "gateway_reference": "test-lookup-receipt",
            "reason": "provider lookup",
            "evidence": {"lookup": "test_receipt"},
        },
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    data = evidence(first)
    assert data["record"]["status"] == "confirmed"
    assert data["budget"]["reserved_amount"] == "0.0"
    assert [e["type"] for e in data["events"]][-1] == "execution_confirmed"


def test_reports_are_actor_token_scoped_and_idempotent():
    scope = setup()
    first = propose(scope)
    token, receipt = confirm(first)
    before = len(evidence(first)["events"])
    assert (
        report(
            first,
            token,
            "confirmed",
            gateway_reference=receipt,
            evidence={"source": "test_provider"},
        ).status_code
        == 200
    )
    assert len(evidence(first)["events"]) == before
    assert (
        report(
            first,
            "wrong-token",
            "confirmed",
            gateway_reference=receipt,
            evidence={"source": "test_provider"},
        ).status_code
        == 403
    )
    assert (
        report(
            first,
            token,
            "confirmed",
            gateway_reference="conflicting-receipt",
            evidence={"source": "test_provider"},
        ).status_code
        == 409
    )


def test_scoped_idempotency_conflicts_and_environment_authorization():
    first_scope = setup()
    other_scope = setup()
    env, agent, _ = first_scope
    key = client.post(
        "/v1/api-keys",
        json={"env_id": env["id"], "name": "executor", "scope": "ingest"},
        headers=AUTH,
    ).json()["data"]["secret"]
    headers = {
        "Authorization": f"Bearer {key}",
        "Idempotency-Mode": "scoped",
        "Idempotency-Key": "same-key",
    }
    body = {
        "env_id": env["id"],
        "agent_id": agent["id"],
        "principal_id": "p",
        "merchant": "m",
        "amount": 1,
        "request_id": "logical",
    }
    first = client.post("/v1/payments", json=body, headers=headers)
    assert first.status_code == 200
    assert (
        client.post("/v1/payments", json=body, headers=headers).json()["data"]["id"]
        == first.json()["data"]["id"]
    )
    assert (
        client.post(
            "/v1/payments", json={**body, "amount": 2}, headers=headers
        ).status_code
        == 409
    )
    other = propose(other_scope, 1)
    assert (
        client.get(f"/v1/payments/{other['id']}/execution", headers=headers).status_code
        == 403
    )
    assert claim(other, headers=headers).status_code == 403
    assert (
        client.get(
            f"/v1/payments?env_id={other_scope[0]['id']}&request_id=logical",
            headers=headers,
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/decisions", json={"payment_id": other["id"]}, headers=headers
        ).status_code
        == 403
    )
    new = client.post(
        "/v1/payments",
        json={**body, "env_id": other_scope[0]["id"], "agent_id": other_scope[1]["id"]},
        headers={**AUTH, "Idempotency-Mode": "scoped", "Idempotency-Key": "same-key"},
    )
    assert (
        new.status_code == 200
        and new.json()["data"]["id"] != first.json()["data"]["id"]
    )


def test_policy_change_between_claim_and_submit_is_blocked():
    scope = setup()
    first = propose(scope)
    token = claim(first).json()["data"]["claim_token"]
    policy = scope[2]
    client.put(
        f"/v1/policies/{policy['id']}/draft",
        json={"document": {"hard_limit": 50, "review_threshold": 10}},
        headers=AUTH,
    )
    client.post(f"/v1/policies/{policy['id']}/publish", headers=AUTH)
    response = report(first, token, "submitted")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "policy_revision_changed"
    assert evidence(first)["reservation"]["status"] == "held"


def test_denied_claim_persists_context_review_and_business_event():
    scope = setup({"review_threshold": 10, "hard_limit": 100})
    first = propose(scope, 20)
    response = claim(first)
    assert response.status_code == 200
    assert not response.json()["data"]["acquired"]
    decisions = client.get(
        f"/v1/decisions?payment_id={first['id']}", headers=AUTH
    ).json()["data"]
    assert decisions[-1]["verdict"] == "needs_review"
    reviews = client.get(f"/v1/reviews?env_id={scope[0]['id']}", headers=AUTH).json()[
        "data"
    ]
    assert any(r["payment_id"] == first["id"] and r["state"] == "open" for r in reviews)
    assert "execution_denied" in [e["type"] for e in evidence(first)["events"]]
    with store.lock:
        ctx = store.connection.execute(
            "SELECT request_json FROM decision_contexts WHERE decision_id=?",
            (decisions[-1]["id"],),
        ).fetchone()
    assert json.loads(ctx[0])["amount"] == "2E+1"


def test_legacy_transition_cannot_bypass_budget_or_mandate():
    scope = setup()
    first, second = propose(scope), propose(scope)
    for payment in [first, second]:
        assert (
            client.post(
                f"/v1/payments/{payment['id']}/transition",
                json={"to": "approved"},
                headers=AUTH,
            ).status_code
            == 200
        )
    assert (
        client.post(
            f"/v1/payments/{first['id']}/transition",
            json={"to": "executed"},
            headers=AUTH,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/v1/payments/{second['id']}/transition",
            json={"to": "executed"},
            headers=AUTH,
        ).status_code
        == 409
    )
    assert evidence(first)["execution"] is None
    assert evidence(first)["budget"]["committed_amount"] == "60.0"
    scoped = setup()
    wrong = propose(
        scoped,
        10,
        mandate={"principal_id": "someone-else", "agent_id": scoped[1]["id"]},
    )
    client.post(
        f"/v1/payments/{wrong['id']}/transition", json={"to": "approved"}, headers=AUTH
    )
    assert (
        client.post(
            f"/v1/payments/{wrong['id']}/transition",
            json={"to": "executed"},
            headers=AUTH,
        ).status_code
        == 409
    )


def test_outbox_publication_replay_keeps_one_delivery_and_evidence_is_immutable():
    scope = setup()
    endpoint = client.post(
        "/v1/webhook-endpoints",
        json={
            "env_id": scope[0]["id"],
            "url": "https://example.test/events",
            "events": ["*"],
        },
        headers=AUTH,
    ).json()["data"]
    payment = propose(scope)
    confirm(payment)
    router = get_event_router()
    # Drain unrelated test events as well; no HTTP deliveries run in this test.
    while router.publish_execution_events():
        pass
    before = client.get(
        f"/v1/webhook-deliveries?endpoint_id={endpoint['id']}", headers=AUTH
    ).json()["meta"]["total"]
    assert before >= 3
    assert router.publish_execution_events() == 0
    assert (
        client.get(
            f"/v1/webhook-deliveries?endpoint_id={endpoint['id']}", headers=AUTH
        ).json()["meta"]["total"]
        == before
    )
    with store.lock:
        with pytest.raises(sqlite3.DatabaseError, match="immutable|append-only"):
            store.connection.execute(
                "DELETE FROM execution_events WHERE request_id=?", (payment["id"],)
            )
        store.connection.rollback()
        with pytest.raises(sqlite3.DatabaseError, match="immutable|append-only"):
            store.connection.execute(
                "UPDATE execution_bindings SET policy_json=? WHERE payment_id=?",
                ("{}", payment["id"]),
            )
        store.connection.rollback()


def test_currency_and_scope_do_not_mix_and_expired_unknown_stays_held():
    scope = setup(
        {
            "daily_budget": 100,
            "review_threshold": 100,
            "hard_limit": 1000,
            "budget_warning_ratio": 1,
            "allowed_currencies": ["USD", "EUR"],
        }
    )
    usd = propose(scope)
    token = claim(usd).json()["data"]["claim_token"]
    report(usd, token, "submitted")
    report(usd, token, "unknown")
    eur = propose(scope, currency="EUR")
    assert claim(eur).json()["data"]["acquired"]
    ledger = get_execution_store().ledger
    args = get_execution_store().scope(usd)
    assert ledger.spend_since(scope[1]["id"], 10**15, currency="USD", **args) == 60
    assert ledger.spend_since(scope[1]["id"], 10**15, currency="EUR", **args) == 60


def approve_review(payment):
    decision = client.post(
        "/v1/decisions", json={"payment_id": payment["id"]}, headers=AUTH
    ).json()["data"]
    review = decision["review_id"]
    response = client.post(
        f"/v1/reviews/{review}/approve",
        json={"reviewer_id": "operator", "note": "Reviewed this request"},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    return review


def test_human_approval_cannot_clear_new_history_reason():
    scope = setup(
        {
            "review_threshold": 10,
            "hard_limit": 1000,
            "daily_budget": 100,
            "budget_warning_ratio": 0.8,
        }
    )
    pending = propose(scope, 20)
    approve_review(pending)
    other = propose(scope, 65)
    approve_review(other)
    confirm(other)
    response = claim(pending)
    assert response.status_code == 200 and not response.json()["data"]["acquired"]
    assert "budget" in response.json()["data"]["reason"].lower()
    data = evidence(pending)
    assert data["legacy_state"] == "needs_review"
    assert "authorization_approved" in [event["type"] for event in data["events"]]


def test_human_approval_expiry_and_changed_policy_require_review(monkeypatch):
    import stores.executions as execution_module

    scope = setup({"review_threshold": 10, "hard_limit": 1000})
    pending = propose(scope, 20)
    approve_review(pending)
    original = execution_module.now_ms
    monkeypatch.setattr(execution_module, "now_ms", lambda: original() + 901_000)
    assert not claim(pending).json()["data"]["acquired"]


def test_expired_claim_and_mandate_fail_before_provider_submission(monkeypatch):
    import stores.executions as execution_module
    import paiziq.engine.audit4 as audit_module

    scope = setup()
    payment = propose(scope, 10)
    token = claim(payment).json()["data"]["claim_token"]
    original = execution_module.now_ms
    monkeypatch.setattr(execution_module, "now_ms", lambda: original() + 901_000)
    response = report(payment, token, "submitted")
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "authorization_expired"
    )
    monkeypatch.setattr(execution_module, "now_ms", original)
    mandate = {
        "principal_id": "principal",
        "agent_id": scope[1]["id"],
        "expires_at_ms": original() + 1000,
    }
    payment = propose(scope, 10, mandate=mandate)
    token = claim(payment).json()["data"]["claim_token"]
    clock = audit_module.time.time
    monkeypatch.setattr(audit_module.time, "time", lambda: clock() + 2)
    assert report(payment, token, "submitted").status_code == 409


def test_file_restart_retains_claim_and_imports_legacy_only_once(tmp_path):
    from storage import IngestStore
    from stores.executions import ExecutionStore
    from stores.payments import PaymentStore

    from migrations import apply_migrations, discover_migrations
    from ids import now_ms

    database = tmp_path / "execution-restart.sqlite"
    old_migrations = tmp_path / "old_migrations"
    old_migrations.mkdir()
    for version, source in discover_migrations():
        if version <= 8:
            (old_migrations / source.name).write_text(source.read_text())
    old = sqlite3.connect(database)
    apply_migrations(old, old_migrations)
    old.execute("INSERT INTO organizations VALUES('org_restart','restart',1)")
    old.execute(
        "INSERT INTO environments VALUES('env_restart','org_restart','sandbox','sandbox',1)"
    )
    old.execute(
        "INSERT INTO agents(id,env_id,name,created_at_ms) VALUES('agent_restart','env_restart','worker',1)"
    )
    old.execute(
        "INSERT INTO payments(id,env_id,agent_id,principal_id,merchant,amount,currency,state,created_at_ms,updated_at_ms) VALUES('pay_legacy','env_restart','agent_restart','principal','vendor',25.001,'USD','executed',?,?)",
        (now_ms(), now_ms()),
    )
    old.commit()
    old.close()
    # Opening the old file performs migrations 9–11 before importing evidence.
    storage = IngestStore(str(database))
    payments = PaymentStore(storage.connection, storage.lock)
    legacy = payments.get("pay_legacy")
    authority = ExecutionStore(storage.connection, storage.lock)
    pending = payments.create(
        "env_restart", "agent_restart", "principal", "vendor", 10, "USD", "", None, None
    )
    original = authority.claim(pending["id"], "test")
    assert original["acquired"]
    storage.connection.close()
    restored_storage = IngestStore(str(database))
    restored = ExecutionStore(restored_storage.connection, restored_storage.lock)
    replay = restored.claim(pending["id"], "test")
    assert not replay["acquired"]
    assert replay["record"]["execution_id"] == original["record"]["execution_id"]
    data = restored.evidence(
        PaymentStore(restored_storage.connection, restored_storage.lock).get(
            pending["id"]
        )
    )
    assert data["budget"]["committed_amount"] == "25.001"
    assert Decimal(data["budget"]["reserved_amount"]) == Decimal("10.0")
    assert (
        restored_storage.connection.execute(
            "SELECT COUNT(*) FROM execution_records WHERE request_id=?", (legacy["id"],)
        ).fetchone()[0]
        == 1
    )
    restored_storage.connection.close()


def test_execution_webhook_evidence_is_confined_to_environment():
    owner = setup()
    outsider = setup()
    endpoint = client.post(
        "/v1/webhook-endpoints",
        json={
            "env_id": owner[0]["id"],
            "url": "https://example.test/private-evidence",
            "events": ["*"],
        },
        headers=AUTH,
    ).json()["data"]
    payment = propose(owner)
    confirm(payment)
    while get_event_router().publish_execution_events():
        pass
    deliveries = client.get(
        f"/v1/webhook-deliveries?endpoint_id={endpoint['id']}", headers=AUTH
    ).json()["data"]
    assert deliveries
    key = client.post(
        "/v1/api-keys",
        json={
            "env_id": outsider[0]["id"],
            "name": "other-environment",
            "scope": "read",
        },
        headers=AUTH,
    ).json()["data"]["secret"]
    headers = {"Authorization": f"Bearer {key}"}
    assert (
        client.get(
            f"/v1/webhook-deliveries/{deliveries[0]['id']}", headers=headers
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/v1/webhook-deliveries?endpoint_id={endpoint['id']}", headers=headers
        ).status_code
        == 403
    )
    assert client.get("/v1/webhook-deliveries", headers=headers).json()["data"] == []
    assert (
        client.get(
            f"/v1/webhook-endpoints?env_id={owner[0]['id']}", headers=headers
        ).status_code
        == 403
    )
