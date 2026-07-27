"""Human-review API tests (PZ-101)."""

from __future__ import annotations

import sqlite3
import sys
import threading
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app, store  # noqa: E402
from migrations import apply_migrations  # noqa: E402
from stores.decisions import ReviewStore  # noqa: E402

ADMIN = {"Authorization": "Bearer dev-key"}
client = TestClient(app)


def _name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _open_review() -> tuple[dict, dict, dict]:
    org = client.post(
        "/v1/orgs",
        json={"name": _name("review-org")},
        headers=ADMIN,
    ).json()["data"]
    env = client.post(
        f"/v1/orgs/{org['id']}/environments",
        json={"name": "sandbox", "kind": "sandbox"},
        headers=ADMIN,
    ).json()["data"]
    agent = client.post(
        "/v1/agents",
        json={"env_id": env["id"], "name": _name("review-agent")},
        headers=ADMIN,
    ).json()["data"]
    payment = client.post(
        "/v1/payments",
        json={
            "env_id": env["id"],
            "agent_id": agent["id"],
            "principal_id": "review-user",
            "merchant": "review merchant",
            "amount": 500,
        },
        headers=ADMIN,
    ).json()["data"]
    decision = client.post(
        "/v1/decisions",
        json={"payment_id": payment["id"]},
        headers=ADMIN,
    ).json()["data"]
    assert decision["review_id"]
    return env, payment, decision


def _action(review_id: str, action: str, **body):
    return client.post(
        f"/v1/reviews/{review_id}/{action}",
        json=body,
        headers=ADMIN,
    )


def _key(env_id: str, name: str, role: str, scope: str = "read") -> tuple[dict, dict]:
    record = client.post(
        "/v1/api-keys",
        json={"env_id": env_id, "name": name, "scope": scope, "role": role},
        headers=ADMIN,
    ).json()["data"]
    headers = {"Authorization": f"Bearer {record['secret']}"}
    return record, headers


def test_queue_list_detail_filters_and_enriches_payment():
    env, payment, decision = _open_review()
    response = client.get(
        f"/v1/reviews?state=open&env_id={env['id']}",
        headers=ADMIN,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["meta"]["total"] == 1
    review = payload["data"][0]
    assert review["id"] == decision["review_id"]
    assert review["payment"]["id"] == payment["id"]
    assert review["priority"] == "normal"
    assert review["last_action"] == "opened"
    assert isinstance(review["sla_remaining_ms"], int)
    assert review["sla_breached"] is False

    detail = client.get(f"/v1/reviews/{review['id']}", headers=ADMIN)
    assert detail.json()["data"]["payment"]["amount"] == 500
    assert client.get("/v1/reviews/rev_missing", headers=ADMIN).status_code == 404


def test_claim_release_reassign_and_assignment_conflicts():
    _, _, decision = _open_review()
    review_id = decision["review_id"]
    claimed = _action(review_id, "claim", reviewer_id="alice")
    assert claimed.status_code == 200
    assert claimed.json()["data"]["reviewer_id"] == "alice"
    assert claimed.json()["data"]["last_action"] == "claimed"

    conflict = _action(review_id, "claim", reviewer_id="bob")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "review_assignment_conflict"

    reassigned = _action(
        review_id,
        "reassign",
        reviewer_id="bob",
        note="Alice is out of office",
    )
    assert reassigned.status_code == 200
    assert reassigned.json()["data"]["reviewer_id"] == "bob"
    assert reassigned.json()["data"]["last_action"] == "reassigned"

    wrong_release = _action(review_id, "release", reviewer_id="alice")
    assert wrong_release.status_code == 409
    released = _action(
        review_id,
        "release",
        reviewer_id="bob",
        note="Returning to the queue",
    )
    assert released.status_code == 200
    assert released.json()["data"]["reviewer_id"] is None
    assert released.json()["data"]["last_action"] == "released"


def test_request_info_escalate_and_note_validation():
    _, _, decision = _open_review()
    review_id = decision["review_id"]

    blank = _action(
        review_id,
        "request-more-info",
        reviewer_id="alice",
        note="   ",
    )
    assert blank.status_code == 422
    assert blank.json()["error"]["code"] == "validation_error"

    requested = _action(
        review_id,
        "request-more-info",
        reviewer_id="alice",
        note="Attach the purchase order",
    )
    assert requested.status_code == 200
    assert requested.json()["data"]["state"] == "open"
    assert requested.json()["data"]["last_action"] == "requested_info"

    escalated = _action(
        review_id,
        "escalate",
        reviewer_id="alice",
        note="High-value exception requires lead review",
        priority="urgent",
    )
    assert escalated.status_code == 200
    assert escalated.json()["data"]["priority"] == "urgent"
    assert escalated.json()["data"]["last_action"] == "escalated"

    entries = client.get(
        f"/v1/audit-logs?resource={review_id}",
        headers=ADMIN,
    ).json()["data"]
    notes = {entry["action"]: entry["detail"]["note"] for entry in entries}
    assert notes["review.requested_info"] == "Attach the purchase order"
    assert notes["review.escalated"] == "High-value exception requires lead review"


def test_approve_is_atomic_with_payment_transition_and_audit():
    _, payment, decision = _open_review()
    review_id = decision["review_id"]
    approved = _action(
        review_id,
        "approve",
        reviewer_id="alice",
        note="Purchase order verified",
    )
    assert approved.status_code == 200, approved.text
    review = approved.json()["data"]
    assert review["state"] == "approved"
    assert review["resolved_at_ms"] is not None

    detail = client.get(f"/v1/payments/{payment['id']}", headers=ADMIN).json()["data"]
    assert detail["state"] == "approved"
    assert detail["transitions"][-1]["from"] == "needs_review"
    assert detail["transitions"][-1]["to"] == "approved"
    assert detail["transitions"][-1]["reason"] == "Purchase order verified"

    repeated = _action(
        review_id,
        "approve",
        reviewer_id="alice",
        note="Repeated click",
    )
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "review_not_open"

    actions = store.connection.execute(
        "SELECT action FROM audit_log WHERE resource = ? ORDER BY id",
        (review_id,),
    ).fetchall()
    assert [row[0] for row in actions] == ["review.approved"]


def test_decline_and_reject_alias_resolve_reviews():
    _, payment, decision = _open_review()
    declined = _action(
        decision["review_id"],
        "decline",
        reviewer_id="alice",
        note="Merchant evidence did not match",
    )
    assert declined.status_code == 200
    assert declined.json()["data"]["state"] == "rejected"
    state = client.get(f"/v1/payments/{payment['id']}", headers=ADMIN).json()["data"]["state"]
    assert state == "rejected"

    _, _, second = _open_review()
    rejected = _action(
        second["review_id"],
        "reject",
        reviewer_id="bob",
        note="Rejected through compatibility endpoint",
    )
    assert rejected.status_code == 200
    assert rejected.json()["data"]["state"] == "rejected"


def test_review_mutations_require_review_scope():
    env, _, decision = _open_review()
    developer = client.post(
        "/v1/api-keys",
        json={
            "env_id": env["id"],
            "name": _name("developer"),
            "scope": "ingest",
            "role": "developer",
        },
        headers=ADMIN,
    ).json()["data"]["secret"]
    reviewer_id = _name("reviewer")
    reviewer, _ = _key(env["id"], reviewer_id, "reviewer")

    denied = client.post(
        f"/v1/reviews/{decision['review_id']}/claim",
        json={"reviewer_id": reviewer_id},
        headers={"Authorization": f"Bearer {developer}"},
    )
    assert denied.status_code == 403

    allowed = client.post(
        f"/v1/reviews/{decision['review_id']}/claim",
        json={"reviewer_id": reviewer_id},
        headers={"Authorization": f"Bearer {reviewer['secret']}"},
    )
    assert allowed.status_code == 200


def test_identity_endpoint_distinguishes_bootstrap_and_managed_keys():
    bootstrap = client.get("/v1/reviews/identity", headers=ADMIN)
    assert bootstrap.status_code == 200
    assert bootstrap.json()["data"] == {
        "reviewer_id": None,
        "role": "admin",
        "env_id": None,
        "managed_identity": False,
    }

    env, _, _ = _open_review()
    reviewer_id = _name("identity")
    _, headers = _key(env["id"], reviewer_id, "reviewer")
    managed = client.get("/v1/reviews/identity", headers=headers)
    assert managed.status_code == 200
    assert managed.json()["data"] == {
        "reviewer_id": reviewer_id,
        "role": "reviewer",
        "env_id": env["id"],
        "managed_identity": True,
    }


def test_managed_key_is_confined_to_its_environment():
    own_env, _, _ = _open_review()
    other_env, _, other_decision = _open_review()
    reviewer_id = _name("tenant-reviewer")
    _, headers = _key(own_env["id"], reviewer_id, "reviewer")

    cross_list = client.get(
        f"/v1/reviews?env_id={other_env['id']}",
        headers=headers,
    )
    assert cross_list.status_code == 403
    assert cross_list.json()["error"]["code"] == "forbidden"

    cross_detail = client.get(
        f"/v1/reviews/{other_decision['review_id']}",
        headers=headers,
    )
    assert cross_detail.status_code == 403

    cross_claim = client.post(
        f"/v1/reviews/{other_decision['review_id']}/claim",
        json={"reviewer_id": reviewer_id},
        headers=headers,
    )
    assert cross_claim.status_code == 403

    own_queue = client.get("/v1/reviews?state=open", headers=headers).json()["data"]
    assert own_queue
    assert all(item["payment"]["env_id"] == own_env["id"] for item in own_queue)


def test_managed_identity_and_owner_are_enforced_for_assignment_actions():
    env, _, decision = _open_review()
    review_id = decision["review_id"]
    alice_record, alice = _key(env["id"], _name("alice"), "reviewer")
    bob_record, bob = _key(env["id"], _name("bob"), "reviewer")
    alice_id = alice_record["name"]
    bob_id = bob_record["name"]

    impersonation = client.post(
        f"/v1/reviews/{review_id}/claim",
        json={"reviewer_id": bob_id},
        headers=alice,
    )
    assert impersonation.status_code == 403

    claimed = client.post(
        f"/v1/reviews/{review_id}/claim",
        json={"reviewer_id": alice_id},
        headers=alice,
    )
    assert claimed.status_code == 200

    spoofed_release = client.post(
        f"/v1/reviews/{review_id}/release",
        json={"reviewer_id": alice_id},
        headers=bob,
    )
    assert spoofed_release.status_code == 403

    wrong_owner_release = client.post(
        f"/v1/reviews/{review_id}/release",
        json={"reviewer_id": bob_id},
        headers=bob,
    )
    assert wrong_owner_release.status_code == 409

    wrong_owner_reassign = client.post(
        f"/v1/reviews/{review_id}/reassign",
        json={"reviewer_id": bob_id, "note": "Trying to take another queue item"},
        headers=bob,
    )
    assert wrong_owner_reassign.status_code == 409

    reassigned = client.post(
        f"/v1/reviews/{review_id}/reassign",
        json={"reviewer_id": bob_id, "note": "Passing to the next reviewer"},
        headers=alice,
    )
    assert reassigned.status_code == 200
    assert reassigned.json()["data"]["reviewer_id"] == bob_id

    released = client.post(
        f"/v1/reviews/{review_id}/release",
        json={"reviewer_id": bob_id, "note": "Returning to shared queue"},
        headers=bob,
    )
    assert released.status_code == 200

    rows = store.connection.execute(
        "SELECT actor, action, detail FROM audit_log WHERE resource = ? ORDER BY id",
        (review_id,),
    ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [
        (f"key:{alice_record['id']}", "review.claimed"),
        (f"key:{alice_record['id']}", "review.reassigned"),
        (f"key:{bob_record['id']}", "review.released"),
    ]


def test_generic_payment_transition_cannot_bypass_open_review():
    env, payment, decision = _open_review()
    _, developer = _key(env["id"], _name("developer"), "developer", "ingest")

    bypass = client.post(
        f"/v1/payments/{payment['id']}/transition",
        json={"to": "approved", "reason": "skip review"},
        headers=developer,
    )
    assert bypass.status_code == 409
    assert bypass.json()["error"]["code"] == "review_resolution_required"

    payment_detail = client.get(
        f"/v1/payments/{payment['id']}",
        headers=ADMIN,
    ).json()["data"]
    review_detail = client.get(
        f"/v1/reviews/{decision['review_id']}",
        headers=ADMIN,
    ).json()["data"]
    assert payment_detail["state"] == "needs_review"
    assert review_detail["state"] == "open"


def test_decision_reevaluation_cannot_resolve_an_open_review():
    env, payment, decision = _open_review()
    policy = client.post(
        "/v1/policies",
        json={
            "env_id": env["id"],
            "name": _name("permissive"),
            "document": {"review_threshold": 1000, "hard_limit": 2000},
        },
        headers=ADMIN,
    ).json()["data"]
    assert client.post(
        f"/v1/policies/{policy['id']}/publish",
        headers=ADMIN,
    ).status_code == 200

    reevaluation = client.post(
        "/v1/decisions",
        json={"payment_id": payment["id"]},
        headers=ADMIN,
    )
    assert reevaluation.status_code == 409
    assert reevaluation.json()["error"]["code"] == "review_resolution_required"
    decisions = client.get(
        f"/v1/decisions?payment_id={payment['id']}",
        headers=ADMIN,
    ).json()
    assert decisions["meta"]["total"] == 1
    assert decisions["data"][0]["id"] == decision["id"]


def test_resolution_rolls_back_all_writes_when_review_update_fails():
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn)
    conn.executescript(
        """
        INSERT INTO organizations VALUES ('org_atomic', 'Atomic', 1);
        INSERT INTO environments
            VALUES ('env_atomic', 'org_atomic', 'sandbox', 'sandbox', 1);
        INSERT INTO agents (id, env_id, name, created_at_ms)
            VALUES ('agt_atomic', 'env_atomic', 'agent', 1);
        INSERT INTO payments (
            id, env_id, agent_id, principal_id, merchant, amount, state,
            created_at_ms, updated_at_ms
        ) VALUES (
            'pay_atomic', 'env_atomic', 'agt_atomic', 'user', 'merchant', 5,
            'needs_review', 1, 1
        );
        INSERT INTO decisions (
            id, payment_id, verdict, reasons, risk_flags, created_at_ms
        ) VALUES ('dec_atomic', 'pay_atomic', 'needs_review', '[]', '[]', 1);
        INSERT INTO reviews (
            id, payment_id, decision_id, state, created_at_ms, updated_at_ms
        ) VALUES ('rev_atomic', 'pay_atomic', 'dec_atomic', 'open', 1, 1);
        CREATE TRIGGER fail_review_resolution
        BEFORE UPDATE ON reviews
        WHEN NEW.state = 'approved'
        BEGIN
            SELECT RAISE(ABORT, 'injected review failure');
        END;
        """
    )
    conn.commit()
    reviews = ReviewStore(conn, threading.Lock())

    with pytest.raises(sqlite3.IntegrityError, match="injected review failure"):
        reviews.resolve("rev_atomic", "alice", "approved after review", "approved")

    assert conn.in_transaction is False
    assert conn.execute(
        "SELECT state FROM payments WHERE id = 'pay_atomic'"
    ).fetchone()[0] == "needs_review"
    assert conn.execute(
        "SELECT state FROM reviews WHERE id = 'rev_atomic'"
    ).fetchone()[0] == "open"
    assert conn.execute(
        "SELECT COUNT(*) FROM payment_transitions WHERE payment_id = 'pay_atomic'"
    ).fetchone()[0] == 0
