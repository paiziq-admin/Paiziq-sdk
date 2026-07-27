"""Policy management tests (PZ-022): validated documents, immutable
published versions, single active version per environment, and the
decision engine consuming the active policy."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app, store  # noqa: E402
from policy_doc import DOCUMENT_FIELDS  # noqa: E402

AUTH = {"Authorization": "Bearer dev-key"}
client = TestClient(app)

STRICT_DOC = {"review_threshold": 10.0, "hard_limit": 50.0}


def _name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _env() -> dict:
    org = client.post("/v1/orgs", json={"name": _name("org")}, headers=AUTH).json()["data"]
    return client.post(
        f"/v1/orgs/{org['id']}/environments",
        json={"name": "sandbox", "kind": "sandbox"}, headers=AUTH,
    ).json()["data"]


def _policy(env_id: str, document: dict | None = None, name: str | None = None) -> dict:
    r = client.post(
        "/v1/policies",
        json={"env_id": env_id, "name": name or _name("policy"), "document": document},
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _publish(policy_id: str) -> dict:
    r = client.post(f"/v1/policies/{policy_id}/publish", headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_create_policy_normalizes_default_document():
    policy = _policy(_env()["id"])
    assert policy["id"].startswith("pol_")
    assert policy["active_version"] is None and policy["latest_version"] is None
    assert set(policy["draft_document"]) == set(DOCUMENT_FIELDS)
    assert policy["draft_document"]["review_threshold"] == 100.0  # SDK default


def test_create_policy_rejects_invalid_document():
    env = _env()
    r = client.post(
        "/v1/policies",
        json={"env_id": env["id"], "name": _name("p"),
              "document": {"review_threshold": 100.0, "hard_limit": 5.0}},
        headers=AUTH,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    r = client.post(
        "/v1/policies",
        json={"env_id": env["id"], "name": _name("p"), "document": {"not_a_field": 1}},
        headers=AUTH,
    )
    assert r.status_code == 422


def test_create_policy_unknown_env_and_duplicate_name():
    assert client.post(
        "/v1/policies", json={"env_id": "env_missing", "name": "p"}, headers=AUTH
    ).status_code == 404
    env = _env()
    _policy(env["id"], name="dup")
    r = client.post("/v1/policies", json={"env_id": env["id"], "name": "dup"}, headers=AUTH)
    assert r.status_code == 409


def test_publish_creates_immutable_increasing_versions():
    policy = _policy(_env()["id"], STRICT_DOC)
    v1 = _publish(policy["id"])
    assert (v1["version"], v1["is_active"]) == (1, True)
    assert v1["document"]["hard_limit"] == 50.0

    # change draft and publish again — v1 survives untouched, v2 activates
    client.put(
        f"/v1/policies/{policy['id']}/draft",
        json={"document": {"review_threshold": 20.0, "hard_limit": 80.0}}, headers=AUTH,
    )
    versions = client.get(
        f"/v1/policies/{policy['id']}/versions", headers=AUTH
    ).json()["data"]
    assert [v["version"] for v in versions] == [1]

    v2 = _publish(policy["id"])
    assert v2["version"] == 2 and v2["is_active"]
    versions = client.get(
        f"/v1/policies/{policy['id']}/versions", headers=AUTH
    ).json()["data"]
    assert [(v["version"], v["is_active"]) for v in versions] == [(1, False), (2, True)]
    assert versions[0]["document"]["hard_limit"] == 50.0  # snapshot unchanged


def test_single_active_version_per_environment():
    env = _env()
    pol_a = _policy(env["id"], STRICT_DOC)
    pol_b = _policy(env["id"])
    _publish(pol_a["id"])
    _publish(pol_b["id"])
    active = store.connection.execute(
        "SELECT policy_id, version FROM policy_versions WHERE is_active = 1 AND "
        "policy_id IN (SELECT id FROM policies WHERE env_id = ?)",
        (env["id"],),
    ).fetchall()
    assert active == [(pol_b["id"], 1)]


def test_get_version_and_not_found():
    policy = _policy(_env()["id"])
    _publish(policy["id"])
    v = client.get(f"/v1/policies/{policy['id']}/versions/1", headers=AUTH).json()["data"]
    assert v["version"] == 1
    assert client.get(
        f"/v1/policies/{policy['id']}/versions/9", headers=AUTH
    ).status_code == 404
    assert client.get("/v1/policies/pol_missing", headers=AUTH).status_code == 404


def test_decisions_use_active_policy_version():
    env = _env()
    policy = _policy(env["id"], STRICT_DOC)
    _publish(policy["id"])
    agent = client.post(
        "/v1/agents", json={"env_id": env["id"], "name": _name("agent")}, headers=AUTH
    ).json()["data"]
    payment = client.post(
        "/v1/payments",
        json={"env_id": env["id"], "agent_id": agent["id"], "principal_id": "u1",
              "merchant": "acme corp", "amount": 20.0,
              "intent_description": "small buy"},
        headers=AUTH,
    ).json()["data"]
    decision = client.post(
        "/v1/decisions", json={"payment_id": payment["id"]}, headers=AUTH
    ).json()["data"]
    # 20 > strict review_threshold of 10 — default policy would approve
    assert decision["verdict"] == "needs_review"
    assert decision["policy_version"] == 1


def test_rollback_publishes_new_version_copying_old_content():
    policy = _policy(_env()["id"], STRICT_DOC)
    _publish(policy["id"])  # v1: hard_limit 50
    client.put(
        f"/v1/policies/{policy['id']}/draft",
        json={"document": {"review_threshold": 20.0, "hard_limit": 80.0}}, headers=AUTH,
    )
    _publish(policy["id"])  # v2: hard_limit 80

    r = client.post(
        f"/v1/policies/{policy['id']}/rollback", json={"version": 1}, headers=AUTH
    )
    assert r.status_code == 200, r.text
    v3 = r.json()["data"]
    assert v3["version"] == 3 and v3["is_active"]
    assert v3["document"]["hard_limit"] == 50.0  # copies v1

    detail = client.get(f"/v1/policies/{policy['id']}", headers=AUTH).json()["data"]
    assert detail["active_version"] == 3
    assert detail["draft_document"]["hard_limit"] == 50.0  # draft re-synced
    versions = client.get(
        f"/v1/policies/{policy['id']}/versions", headers=AUTH
    ).json()["data"]
    assert [v["version"] for v in versions] == [1, 2, 3]  # history intact


def test_rollback_unknown_version_404():
    policy = _policy(_env()["id"])
    r = client.post(
        f"/v1/policies/{policy['id']}/rollback", json={"version": 4}, headers=AUTH
    )
    assert r.status_code == 404


def test_compare_versions_and_draft():
    policy = _policy(_env()["id"], STRICT_DOC)
    _publish(policy["id"])  # v1
    client.put(
        f"/v1/policies/{policy['id']}/draft",
        json={"document": {"review_threshold": 10.0, "hard_limit": 80.0,
                           "merchant_blocklist": ["darkpool"]}},
        headers=AUTH,
    )
    _publish(policy["id"])  # v2

    r = client.get(
        f"/v1/policies/{policy['id']}/versions/compare?base=1&target=2", headers=AUTH
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert (body["base"], body["target"]) == (1, 2)
    assert body["changes"] == {
        "hard_limit": {"base": 50.0, "target": 80.0},
        "merchant_blocklist": {"base": [], "target": ["darkpool"]},
    }

    # draft as a reference, and identical docs → empty diff
    r = client.get(
        f"/v1/policies/{policy['id']}/versions/compare?base=2&target=draft", headers=AUTH
    )
    assert r.json()["data"]["changes"] == {}


def test_compare_invalid_references():
    policy = _policy(_env()["id"])
    assert client.get(
        f"/v1/policies/{policy['id']}/versions/compare?base=zzz&target=1", headers=AUTH
    ).status_code == 422
    assert client.get(
        f"/v1/policies/{policy['id']}/versions/compare?base=1&target=2", headers=AUTH
    ).status_code == 404  # no versions published yet


def _simulate(payload: dict) -> "object":
    return client.post("/v1/policies/simulate", json=payload, headers=AUTH)


def test_simulate_inline_document_and_default():
    payment = {"merchant": "acme corp", "amount": 20.0}
    r = _simulate({"payment": payment, "document": STRICT_DOC})
    body = r.json()["data"]
    assert r.status_code == 200, r.text
    assert body["verdict"] == "needs_review"  # 20 > strict threshold 10
    assert body["policy_source"] == {"type": "inline"}
    assert body["persisted"] is False

    r = _simulate({"payment": payment})  # engine default policy
    body = r.json()["data"]
    assert body["verdict"] == "approved"
    assert body["policy_source"] == {"type": "default"}


def test_simulate_draft_vs_published_version():
    policy = _policy(_env()["id"], STRICT_DOC)
    _publish(policy["id"])  # v1 strict
    client.put(
        f"/v1/policies/{policy['id']}/draft",
        json={"document": {"review_threshold": 100.0, "hard_limit": 1000.0}},
        headers=AUTH,
    )
    payment = {"merchant": "acme corp", "amount": 20.0}

    draft = _simulate(
        {"payment": payment, "policy_id": policy["id"], "use_draft": True}
    ).json()["data"]
    assert draft["verdict"] == "approved"
    assert draft["policy_source"]["type"] == "draft"

    v1 = _simulate(
        {"payment": payment, "policy_id": policy["id"], "version": 1}
    ).json()["data"]
    assert v1["verdict"] == "needs_review"
    assert v1["policy_source"] == {
        "type": "version", "policy_id": policy["id"], "version": 1,
    }

    latest = _simulate({"payment": payment, "policy_id": policy["id"]}).json()["data"]
    assert latest["policy_source"]["version"] == 1  # latest published


def test_simulate_env_active_policy_and_rejection():
    env = _env()
    policy = _policy(env["id"], STRICT_DOC)
    _publish(policy["id"])
    r = _simulate(
        {"payment": {"merchant": "acme corp", "amount": 500.0}, "env_id": env["id"]}
    )
    body = r.json()["data"]
    assert body["verdict"] == "rejected"  # 500 > strict hard_limit 50
    assert body["policy_source"]["type"] == "active"
    assert body["reasons"]


def test_simulate_never_persists():
    before = store.connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    _simulate({"payment": {"merchant": "acme corp", "amount": 20.0}})
    after = store.connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    assert after == before


def test_simulate_error_cases():
    env = _env()
    policy = _policy(env["id"])  # nothing published
    payment = {"merchant": "acme corp", "amount": 20.0}
    assert _simulate({"payment": payment, "policy_id": "pol_missing"}).status_code == 404
    assert _simulate({"payment": payment, "policy_id": policy["id"]}).status_code == 409
    assert _simulate(
        {"payment": payment, "policy_id": policy["id"], "version": 5}
    ).status_code == 404
    assert _simulate({"payment": payment, "env_id": env["id"]}).status_code == 404
    assert _simulate(
        {"payment": payment, "policy_id": policy["id"], "use_draft": True, "version": 1}
    ).status_code == 422
    assert _simulate({"payment": payment, "use_draft": True}).status_code == 422
    assert _simulate(
        {"payment": payment, "document": {"hard_limit": 5.0, "review_threshold": 50.0}}
    ).status_code == 422


def test_rollback_writes_audit_log():
    policy = _policy(_env()["id"], STRICT_DOC)
    _publish(policy["id"])
    client.post(f"/v1/policies/{policy['id']}/rollback", json={"version": 1}, headers=AUTH)
    rows = store.connection.execute(
        "SELECT action FROM audit_log WHERE resource = ? ORDER BY id", (policy["id"],)
    ).fetchall()
    assert [r[0] for r in rows] == ["policy.create", "policy.publish", "policy.rollback"]


def test_policy_mutations_write_audit_log():
    policy = _policy(_env()["id"])
    updated = client.put(
        f"/v1/policies/{policy['id']}/draft",
        json={
            "document": {"merchant_allowlist": ["trusted merchant"]},
            "reason": "Approve the finance-owned merchant list",
        },
        headers=AUTH,
    )
    assert updated.status_code == 200, updated.text
    _publish(policy["id"])
    rows = store.connection.execute(
        "SELECT action, detail FROM audit_log WHERE resource = ? ORDER BY id",
        (policy["id"],),
    ).fetchall()
    assert [r[0] for r in rows] == [
        "policy.create",
        "policy.draft_update",
        "policy.publish",
    ]
    assert "Approve the finance-owned merchant list" in rows[1][1]

    blank = client.put(
        f"/v1/policies/{policy['id']}/draft",
        json={"document": {}, "reason": "   "},
        headers=AUTH,
    )
    assert blank.status_code == 422
