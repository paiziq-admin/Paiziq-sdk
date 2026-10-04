"""Payment-agent workflow integration lane.

# AC: A simulated payment agent using the Paiziq SDK persists an approved,
# needs_review, and rejected payment under one published policy, and the
# control plane returns the decision, threshold flag, review, and trace
# the dashboard reads.
# Behavior: SDK review plus control-plane payment/decision -> persisted verdicts,
# traces, and review -> dashboard-shaped reads match the local decision.
# @category: integration
# @lane: integration
# @dependency: paiziq SDK, ingest API, SQLite
# @complexity: high
# ROI: 110

Runs under make ingest-test. The browser lanes live in the dashboard repo.
"""

from __future__ import annotations

import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
import uvicorn

_INGEST = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_INGEST))
sys.path.insert(0, str(_TESTS))

from app import app  # noqa: E402
from e2e_support.contracts import dashboard_checks, validate  # noqa: E402
from e2e_support.scenario import TRANSACTIONS  # noqa: E402
from e2e_support.simulated_agent import SimulatedPaymentAgent  # noqa: E402
from paiziq.transport import RetryPolicy, SyncHTTPTransport  # noqa: E402


@pytest.fixture(scope="module")
def base_url():
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="e2e-uvicorn", daemon=True)
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


@pytest.fixture(scope="module")
def workflow(base_url):
    return SimulatedPaymentAgent(base_url, "dev-key").run()


def _transport(base_url: str, api_key: str = "dev-key") -> SyncHTTPTransport:
    return SyncHTTPTransport(base_url, api_key=api_key, retry=RetryPolicy(max_attempts=1))


# AC: Local SDK verdicts and the published-policy control-plane verdicts match,
# the approved payment is executed, the threshold payment opens a review, and
# the trace is findable the way the dashboard searches.
# Behavior: Simulated agent runs three payments -> ingest persists decisions
# and spans -> local verdict equals server verdict and search returns the trace.
# @category: integration
# @lane: integration
# @dependency: paiziq SDK, ingest API, SQLite
# @complexity: high
# ROI: 110
def test_sdk_decision_trace_and_review_persist(workflow, base_url):
    by_key = {item["key"]: item for item in workflow["transactions"]}
    expected = {item["key"]: item for item in TRANSACTIONS}
    assert workflow["policy"]["version"] == 1
    transport = _transport(base_url)

    for key, spec in expected.items():
        row = by_key[key]
        assert row["local_verdict"] == spec["expected_verdict"]
        assert row["local_reasons"] == spec["expected_reasons"]
        assert row["local_risk_flags"] == spec["expected_flags"]
        assert row["verdict"] == spec["expected_verdict"]
        assert row["reasons"] == spec["expected_reasons"]
        assert row["risk_flags"] == spec["expected_flags"]
        assert row["policy_version"] == 1
        assert row["state"] == spec["expected_state"]
        detail = transport.get(f"/v1/payments/{row['payment_id']}").json()["data"]
        assert detail["state"] == spec["expected_state"]
        assert detail["request_id"] == row["request_id"]
        assert row["request_id"].startswith(spec["request_id"])
        trace = transport.get(f"/v1/traces/{row['trace_id']}").json()
        names = [span["name"] for span in trace["spans"]]
        assert "paiziq.review_payment" in names

    review = by_key["t2"]
    assert review["review_id"]
    reviews = transport.get(
        f"/v1/reviews?state=open&env_id={workflow['environment']['id']}"
    ).json()
    match = next(item for item in reviews["data"] if item["id"] == review["review_id"])
    assert match["payment_id"] == review["payment_id"]
    assert match["sla_deadline_ms"]
    assert "sla_remaining_ms" in match

    direct = transport.get(f"/v1/traces/{review['request_id']}").json()
    assert direct["spans"] == []
    found = transport.get(
        f"/v1/search/events?q=%22{review['request_id']}%22&limit=10"
    ).json()
    assert found["success"] is True
    trace_ids = {item["trace_id"] for item in found["data"]}
    assert review["trace_id"] in trace_ids

    # Repeat seeding against the same database must not reuse a prior payment
    # or make event search resolve another run's trace.
    repeated = SimulatedPaymentAgent(base_url, "dev-key").run()
    assert {row["payment_id"] for row in repeated["transactions"]}.isdisjoint(
        row["payment_id"] for row in workflow["transactions"]
    )
    for row in repeated["transactions"]:
        found = transport.get(
            f"/v1/search/events?q=%22{row['request_id']}%22&limit=10"
        ).json()
        assert {item["trace_id"] for item in found["data"]} == {row["trace_id"]}


def test_dashboard_contract_shapes(workflow, base_url):
    """Dashboard reads return the envelope and fields the client renders.

    # AC: Journey reads return status 200 and the fields the dashboard renders.
    # Behavior: Seeded workflow -> GET dashboard endpoints -> required fields present.
    # @category: integration
    # @lane: integration
    # @dependency: ingest API
    # @complexity: medium
    # ROI: 60
    """
    transport = _transport(base_url)
    for method, path, body, kind in dashboard_checks(workflow):
        response = transport.request(method, path, json_body=body)
        validate(kind, response.status, response.json(), workflow)


def test_workflow_error_statuses(base_url):
    """Invalid calls return 404, 409, 403, and 422.

    # AC: Missing payment, executed payment, open review, bad key, and bad
    # policy document return the documented error statuses.
    # Behavior: Invalid control-plane calls -> 404, 409, 403, and 422.
    # @category: edge-case
    # @lane: integration
    # @dependency: ingest API
    # @complexity: medium
    # ROI: 55
    """
    transport = _transport(base_url)
    assert transport.get("/v1/payments/pay_missing").status == 404
    missing = transport.post("/v1/decisions", {"payment_id": "pay_missing"})
    assert missing.status == 404
    assert missing.json()["error"]["code"] == "not_found"
    denied = _transport(base_url, "wrong-key").get("/v1/notifications")
    assert denied.status == 403

    suffix = uuid.uuid4().hex[:8]
    org = transport.post("/v1/orgs", {"name": f"e2e-err-{suffix}"}).json()["data"]
    env = transport.post(
        f"/v1/orgs/{org['id']}/environments",
        {"name": "errors", "kind": "sandbox"},
    ).json()["data"]
    agent = transport.post(
        "/v1/agents", {"env_id": env["id"], "name": "error-agent"}
    ).json()["data"]
    invalid = transport.post(
        "/v1/policies",
        {"env_id": env["id"], "name": "bad", "document": {"not_a_field": 1}},
    )
    assert invalid.status == 422
    assert invalid.json()["error"]["code"] == "validation_error"
    policy = transport.post(
        "/v1/policies",
        {
            "env_id": env["id"],
            "name": "loose-then-tight",
            "document": {
                "review_threshold": 100,
                "hard_limit": 1000,
                "known_merchants": ["acme corp"],
                "merchant_blocklist": [],
                "allowed_currencies": ["USD"],
            },
        },
    ).json()["data"]
    transport.post(f"/v1/policies/{policy['id']}/publish")
    payment = transport.post(
        "/v1/payments",
        {
            "env_id": env["id"],
            "agent_id": agent["id"],
            "principal_id": "user-42",
            "merchant": "acme corp",
            "amount": 10,
            "intent_description": "small",
        },
    ).json()["data"]
    first = transport.post("/v1/decisions", {"payment_id": payment["id"]})
    assert first.json()["data"]["verdict"] == "approved"
    transport.post(
        f"/v1/payments/{payment['id']}/transition",
        {"to": "executed", "reason": "recorded for the error case"},
    )
    again = transport.post("/v1/decisions", {"payment_id": payment["id"]})
    assert again.status == 409
    assert again.json()["error"]["code"] == "invalid_state_transition"
    held = transport.post(
        "/v1/payments",
        {
            "env_id": env["id"],
            "agent_id": agent["id"],
            "principal_id": "user-42",
            "merchant": "acme corp",
            "amount": 180,
            "intent_description": "over threshold",
        },
    ).json()["data"]
    opened = transport.post("/v1/decisions", {"payment_id": held["id"]}).json()["data"]
    assert opened["verdict"] == "needs_review" and opened["review_id"]
    transport.request(
        "PUT",
        f"/v1/policies/{policy['id']}/draft",
        {
            "document": {
                "review_threshold": 10000,
                "hard_limit": 20000,
                "known_merchants": ["acme corp"],
            },
            "reason": "raise threshold",
        },
    )
    transport.post(f"/v1/policies/{policy['id']}/publish")
    blocked = transport.post("/v1/decisions", {"payment_id": held["id"]})
    assert blocked.status == 409
    assert blocked.json()["error"]["code"] == "review_resolution_required"
