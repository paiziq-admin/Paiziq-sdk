"""Phase 0: verify side effects and durable evidence across races and failures."""
from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal

import pytest

from paiziq import (
    BudgetTracker,
    DecisionStatus,
    ExecutionConflict,
    GatewayOutcome,
    LedgerBudgetStore,
    Mandate,
    PaiziqSDK,
    PaymentPolicy,
    PaymentRequest,
    SQLiteExecutionLedger,
)
from paiziq.audit import MockGateway
from paiziq.execution import money_decimal, request_digest


def request(**changes):
    return PaymentRequest(**(dict(agent_id="agent", principal_id="human", merchant="acme", amount=60) | changes))


def sdk(ledger=None, gateway=None, **kwargs):
    policy = kwargs.pop("policy", PaymentPolicy(daily_budget=100, review_threshold=1000, hard_limit=1000))
    return PaiziqSDK(policy=policy, execution_ledger=ledger, gateway=gateway, exporters=[], **kwargs)


def test_two_preapproved_sixty_dollar_payments_cannot_exceed_one_hundred():
    gateway = MockGateway()
    client = sdk(gateway=gateway)
    a, b = request(), request()
    assert client.review_payment(a).approved and client.review_payment(b).approved
    assert client.execute_payment(a).executed
    assert not client.execute_payment(b).executed
    assert len(gateway.charges) == 1
    assert client.budget_tracker.daily_spend("agent") == 60


def test_atomic_reservation_prevents_two_workers_consuming_final_budget(tmp_path):
    path, gateway, barrier = tmp_path / "execution.sqlite", MockGateway(), threading.Barrier(2)
    clients = [sdk(SQLiteExecutionLedger(path), gateway) for _ in range(2)]
    requests = [request(), request()]
    for client, payment in zip(clients, requests):
        client.review_payment(payment)

    def execute(i):
        barrier.wait(timeout=5)
        return clients[i].execute_payment(requests[i])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, range(2)))
    assert sum(result.executed for result in results) == 1
    assert len(gateway.charges) == 1


def test_concurrent_same_request_returns_claim_without_a_second_call(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class SlowGateway(MockGateway):
        def charge(self, payment):
            entered.set()
            assert release.wait(5)
            return super().charge(payment)

    gateway, path, payment = SlowGateway(), tmp_path / "execution.sqlite", request()
    first, second = [sdk(SQLiteExecutionLedger(path), gateway) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(first.execute_payment, payment)
        assert entered.wait(5)
        repeat = second.execute_payment(payment)
        assert not repeat.executed and repeat.status == "submitted" and repeat.replayed
        release.set()
        completed = pending.result(timeout=5)
    assert completed.executed and len(gateway.charges) == 1
    assert second.execute_payment(payment).gateway_reference == completed.gateway_reference


def test_success_is_replayed_after_restart(tmp_path):
    path, gateway, payment = tmp_path / "execution.sqlite", MockGateway(), request()
    first = sdk(SQLiteExecutionLedger(path), gateway)
    result = first.execute_payment(payment)
    first.execution_ledger.close()
    second = sdk(SQLiteExecutionLedger(path), MockGateway())
    replayed = second.execute_payment(payment)
    assert replayed.executed and replayed.replayed
    assert replayed.gateway_reference == result.gateway_reference
    assert not second.gateway.charges


def test_timeout_retains_exposure_across_restart_until_provider_lookup(tmp_path):
    class TimeoutAfterCharge(MockGateway):
        def charge_idempotent(self, payment, idempotency_key):
            super().charge_idempotent(payment, idempotency_key)
            raise TimeoutError("Response lost after provider accepted")

    path, gateway, payment = tmp_path / "execution.sqlite", TimeoutAfterCharge(), request()
    first = sdk(SQLiteExecutionLedger(path), gateway)
    result = first.execute_payment(payment)
    assert result.status == "unknown" and not result.executed
    second = sdk(SQLiteExecutionLedger(path), gateway)
    assert second.execute_payment(payment).status == "unknown"
    assert not second.execute_payment(request()).executed
    reconciled = second.reconcile_payment(payment.request_id)
    assert reconciled.status == "confirmed" and reconciled.executed
    assert second.execute_payment(payment).gateway_reference == reconciled.gateway_reference
    assert len(gateway.charges) == 1


def test_gateway_exception_is_unknown_unless_explicitly_declined():
    class LegacyGateway:
        name = "legacy"
        calls = 0

        def charge(self, payment):
            self.calls += 1
            raise RuntimeError("Connection closed")

    gateway, payment = LegacyGateway(), request()
    client = sdk(gateway=gateway)
    assert client.execute_payment(payment).status == "unknown"
    assert client.execute_payment(payment).status == "unknown"
    assert client.reconcile_payment(payment.request_id).status == "unknown"
    assert gateway.calls == 1


def test_decline_releases_capacity_but_same_logical_action_never_retries():
    gateway, payment = MockGateway(fail=True), request()
    client = sdk(gateway=gateway)
    assert client.execute_payment(payment).status == "failed"
    gateway.fail = False
    assert client.execute_payment(payment).status == "failed"
    assert client.execute_payment(request()).executed
    assert len(gateway.charges) == 1


def test_unknown_does_not_age_out_with_daily_or_monthly_window():
    ledger, payment = SQLiteExecutionLedger(), request()
    claim = ledger.claim(payment, request_digest=request_digest(payment), policy_digest="p1", decision_id="d1", now_ms=1)
    assert claim.acquired
    ledger.transition(payment.request_id, "submitted", now_ms=2)
    ledger.transition(payment.request_id, "unknown", now_ms=3)
    # More than a year later, the unresolved exposure is still reserved.
    assert ledger.spend_since("agent", 366 * 86_400_000) == Decimal("60")
    assert ledger.tx_count_since("agent", 366 * 86_400_000) == 1
    other = request()
    assert not ledger.claim(other, request_digest=request_digest(other), policy_digest="p1", decision_id="d2", daily_budget=100, now_ms=367 * 86_400_000).acquired
    ledger.transition(payment.request_id, "failed", evidence={"source": "provider_lookup", "receipt": "declined"})
    assert ledger.spend_since("agent", 0) == 0


def test_confirmed_provider_response_survives_accounting_failure_and_reconciles():
    class FlakyLedger(SQLiteExecutionLedger):
        fail_confirmation = True

        def transition(self, request_id, status, **kwargs):
            if status == "confirmed" and self.fail_confirmation:
                raise OSError("disk temporarily unavailable")
            return super().transition(request_id, status, **kwargs)

    ledger, gateway, payment = FlakyLedger(), MockGateway(), request()
    client = sdk(ledger, gateway)
    result = client.execute_payment(payment)
    assert result.executed and result.status == "confirmed" and result.accounting_pending
    assert result.gateway_reference
    assert ledger.get(payment.request_id).status == "submitted"
    assert not client.execute_payment(payment).executed
    assert len(gateway.charges) == 1
    ledger.fail_confirmation = False
    assert client.reconcile_payment(payment.request_id).executed
    assert client.execute_payment(payment).executed
    assert len(gateway.charges) == 1


def test_export_and_optional_audit_failure_do_not_hide_charge_or_durable_evidence():
    class BrokenProjection:
        def append(self, record):
            raise RuntimeError("Audit mirror unavailable")

    class BrokenExporter:
        def export(self, spans):
            raise RuntimeError("Trace export unavailable")

        def shutdown(self):
            pass

    ledger, gateway, payment = SQLiteExecutionLedger(), MockGateway(), request()
    client = PaiziqSDK(execution_ledger=ledger, gateway=gateway, audit_store=BrokenProjection(), exporters=[BrokenExporter()])
    result = client.execute_payment(payment)
    assert result.executed and result.gateway_reference
    assert ledger.get(payment.request_id).status == "confirmed"
    assert "execution_confirmed" in {event["event_type"] for event in ledger.pending_events()}


def test_authority_failure_never_uses_fail_open():
    from paiziq import FailureMode

    class BrokenAuthority:
        def get(self, request_id, **kwargs):
            raise OSError("ledger down")

    gateway = MockGateway()
    client = sdk(BrokenAuthority(), gateway, failure_mode=FailureMode.FAIL_OPEN)
    result = client.execute_payment(request())
    assert not result.executed and "authority_unavailable" in result.error
    assert not gateway.charges


def test_org_environment_agent_and_currency_have_separate_capacity():
    ledger, gateway = SQLiteExecutionLedger(), MockGateway()
    policy = PaymentPolicy(daily_budget=100, allowed_currencies={"USD", "EUR"}, review_threshold=1000, hard_limit=1000)
    first = sdk(ledger, gateway, policy=policy, org_id="org1", env_id="prod")
    payment = request(request_id="logical-action")
    assert first.execute_payment(payment).executed
    assert first.execute_payment(request(currency="EUR")).executed
    assert first.execute_payment(request(agent_id="another-agent")).executed
    for org, env in (("org1", "test"), ("org2", "prod")):
        client = sdk(ledger, gateway, policy=policy, org_id=org, env_id=env)
        assert client.execute_payment(payment).executed
    assert len(gateway.charges) == 5
    assert not first.execute_payment(request()).executed
    assert ledger.spend_since("agent", 0, org_id="org1", env_id="prod", currency="USD") == 60
    assert ledger.spend_since("agent", 0, org_id="org1", env_id="prod", currency="EUR") == 60


@pytest.mark.parametrize("mutation", [
    lambda p: setattr(p, "amount", 61),
    lambda p: setattr(p, "merchant", "different"),
    lambda p: setattr(p, "category", "premium"),
    lambda p: setattr(p, "intent_description", "different intent"),
    lambda p: p.metadata.update({"tier": "premium"}),
    lambda p: p.mandate.allowed_merchants.append("different"),
])
def test_full_request_mutation_invalidates_prior_review(mutation):
    gateway = MockGateway()
    client = sdk(gateway=gateway)
    payment = request(mandate=Mandate("human", "agent", max_amount=100, allowed_merchants=["acme"]))
    client.review_payment(payment)
    mutation(payment)
    result = client.execute_payment(payment)
    assert not result.executed and "changed after review" in result.error
    assert not gateway.charges


def test_same_logical_id_with_different_payload_is_a_conflict():
    client, payment = sdk(), request()
    assert client.execute_payment(payment).executed
    result = client.execute_payment(replace(payment, amount=10))
    assert not result.executed and "request_digest_conflict" in result.error
    assert len(client.gateway.charges) == 1


def test_human_approval_bound_to_policy_and_request_revision():
    policy = PaymentPolicy(review_threshold=10, hard_limit=1000)
    client, payment = sdk(policy=policy), request()
    assert client.review_payment(payment).status is DecisionStatus.NEEDS_REVIEW
    client.approve_review(payment.request_id, "reviewer")
    policy.merchant_blocklist.add("acme")
    result = client.execute_payment(payment)
    assert not result.executed and "policy_revision_changed" in result.error
    assert not client.gateway.charges


def test_human_approval_survives_restart_for_same_revision(tmp_path):
    path, payment = tmp_path / "ledger.sqlite", request()
    policy = PaymentPolicy(review_threshold=10, hard_limit=1000)
    first = sdk(SQLiteExecutionLedger(path), policy=policy)
    first.review_payment(payment)
    first.approve_review(payment.request_id, "reviewer")
    second = sdk(SQLiteExecutionLedger(path), policy=policy)
    assert second.execute_payment(payment).executed


def test_human_approval_does_not_clear_new_dynamic_budget_warning():
    policy = PaymentPolicy(review_threshold=10, hard_limit=1000, daily_budget=100)
    client, payment = sdk(policy=policy), request(amount=60)
    client.review_payment(payment)
    client.approve_review(payment.request_id, "reviewer")
    another = request(amount=30)
    client.review_payment(another)
    client.approve_review(another.request_id, "reviewer")
    assert client.execute_payment(another).executed
    assert not client.execute_payment(payment).executed


def test_returned_decision_cannot_mutate_persisted_authorization():
    client, payment = sdk(), request(amount=10000)
    decision = client.review_payment(payment)
    decision.status = DecisionStatus.APPROVED
    decision.reasons.clear()
    assert not client.execute_payment(payment).executed
    assert not client.gateway.charges


def test_expired_review_requires_new_review_and_approval():
    now, payment = [1000], request()
    client = sdk(clock=lambda: now[0], review_ttl_ms=10)
    client.review_payment(payment)
    now[0] += 11
    result = client.execute_payment(payment)
    assert not result.executed and "review_expired" in result.error
    client.review_payment(payment)
    assert client.execute_payment(payment).executed


def test_context_and_review_digest_are_persisted_with_execution():
    client, payment = sdk(clock=lambda: 123_456, policy_version="v7"), request()
    decision = client.review_payment(payment)
    assert client.execute_payment(payment).executed
    record = client.execution_ledger.get(payment.request_id)
    assert record.context["evaluated_at_ms"] == 123_456
    assert record.context["policy_version"] == "v7"
    assert record.context["review_decision_id"] == decision.decision_id
    assert record.request_digest == request_digest(payment)
    assert record.policy_digest and record.context_digest
    assert len(record.context["four_way_audit"]) == 4
    assert all(check["passed"] for check in record.context["four_way_audit"])


def test_business_events_survive_restart_and_outbox_ack_is_scoped(tmp_path):
    path, payment = tmp_path / "ledger.sqlite", request()
    client = sdk(SQLiteExecutionLedger(path), org_id="one", env_id="production")
    assert client.execute_payment(payment).executed
    ledger = SQLiteExecutionLedger(path)
    events = ledger.pending_events(org_id="one", env_id="production")
    confirmed = next(event for event in events if event["event_type"] == "execution_confirmed")
    with pytest.raises(KeyError):
        ledger.acknowledge(confirmed["event_id"], org_id="two", env_id="production")
    ledger.acknowledge(confirmed["event_id"], org_id="one", env_id="production")
    ledger.acknowledge(confirmed["event_id"], org_id="one", env_id="production")
    assert confirmed["event_id"] not in {event["event_id"] for event in ledger.pending_events(org_id="one", env_id="production")}
    assert confirmed in ledger.list_events(payment.request_id, org_id="one", env_id="production")
    assert not ledger.list_events(payment.request_id, org_id="two", env_id="production")


def test_shared_connection_nested_transaction_and_immutable_evidence():
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    lock = threading.RLock()
    ledger = SQLiteExecutionLedger(connection=connection, lock=lock)
    assert connection.row_factory is None
    connection.execute("BEGIN IMMEDIATE")
    event_id = ledger.append_event("evidence", "request", {"amount": 60})
    assert connection.in_transaction
    connection.rollback()
    assert not ledger.list_events()
    event_id = ledger.append_event("evidence", "request", {"amount": 60})
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute("DELETE FROM execution_events WHERE event_id=?", (event_id,))
    connection.rollback()
    payment = request()
    claim = ledger.claim(payment, request_digest=request_digest(payment), policy_digest="p", decision_id="d")
    assert claim.acquired
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("UPDATE execution_records SET request_digest='changed'")
    connection.rollback()


def test_legacy_injected_budget_history_is_preserved_without_double_counting():
    tracker = BudgetTracker()
    tracker.commit("agent", 30)
    client = sdk(budget_tracker=tracker)
    assert client.execute_payment(request(amount=40)).executed
    assert client.budget_tracker.daily_spend("agent") == 70
    assert tracker.daily_spend("agent") == 30
    assert not client.execute_payment(request(amount=40)).executed
    with pytest.raises(ValueError, match="USD only"):
        sdk(budget_tracker=tracker, policy=PaymentPolicy(allowed_currencies={"USD", "EUR"}))


def test_existing_ledger_budget_adapter_reuses_execution_authority():
    ledger = SQLiteExecutionLedger()
    tracker = BudgetTracker(LedgerBudgetStore(ledger))
    tracker.commit("agent", 30)
    client = sdk(budget_tracker=tracker)
    assert client.execution_ledger is ledger
    assert client.execute_payment(request(amount=40)).executed
    assert client.budget_tracker.daily_spend("agent") == 70


@pytest.mark.parametrize(("amount", "currency", "expected"), [("10", "JPY", "10"), ("0.001", "KWD", "0.001"), ("0.01", "USD", "0.01")])
def test_exact_currency_precision(amount, currency, expected):
    assert money_decimal(amount, currency) == Decimal(expected)


@pytest.mark.parametrize(("amount", "currency"), [("0.1", "JPY"), ("0.0001", "KWD"), ("0.001", "USD"), ("NaN", "USD"), ("Infinity", "USD")])
def test_invalid_currency_amount_fails_closed(amount, currency):
    with pytest.raises(ValueError):
        money_decimal(amount, currency)


def test_reserved_claim_after_crash_needs_explicit_no_charge_evidence(tmp_path):
    path, payment = tmp_path / "ledger.sqlite", request()
    ledger = SQLiteExecutionLedger(path)
    ledger.claim(payment, request_digest=request_digest(payment), policy_digest="p", decision_id="d")
    restarted = sdk(SQLiteExecutionLedger(path))
    assert restarted.execute_payment(payment).status == "reserved"
    assert restarted.reconcile_payment(payment.request_id, outcome=GatewayOutcome("unknown")).status == "reserved"
    assert restarted.reconcile_payment(payment.request_id, outcome=GatewayOutcome("failed", error="Provider confirms request was never submitted")).status == "failed"
    assert restarted.execute_payment(request()).executed


def test_unknown_cannot_be_marked_final_without_evidence():
    ledger, payment = SQLiteExecutionLedger(), request()
    ledger.claim(payment, request_digest=request_digest(payment), policy_digest="p", decision_id="d")
    ledger.transition(payment.request_id, "submitted")
    ledger.transition(payment.request_id, "unknown")
    with pytest.raises(ValueError, match="evidence"):
        ledger.transition(payment.request_id, "failed")
    assert ledger.get(payment.request_id).status == "unknown"


def test_confirmed_record_cannot_be_reopened_or_changed():
    client, payment = sdk(), request()
    result = client.execute_payment(payment)
    with pytest.raises(ExecutionConflict):
        client.execution_ledger.transition(payment.request_id, "submitted")
    with pytest.raises(ExecutionConflict):
        client.execution_ledger.transition(payment.request_id, "confirmed", gateway_reference="other-receipt")
    assert client.execution_ledger.get(payment.request_id).gateway_reference == result.gateway_reference


def test_atomic_velocity_claim_blocks_simultaneous_preapproved_requests(tmp_path):
    path, gateway, barrier = tmp_path / "ledger.sqlite", MockGateway(), threading.Barrier(2)
    policy = PaymentPolicy(review_threshold=1000, hard_limit=1000, max_tx_per_hour=1)
    clients = [sdk(SQLiteExecutionLedger(path), gateway, policy=policy) for _ in range(2)]
    payments = [request(), request()]
    for client, payment in zip(clients, payments):
        client.review_payment(payment)

    def execute(index):
        barrier.wait(5)
        return clients[index].execute_payment(payments[index])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, range(2)))
    assert sum(result.executed for result in results) == 1
    assert len(gateway.charges) == 1


def test_fractional_cent_approval_is_not_rounded_into_a_different_payment():
    client = sdk()
    result = client.execute_payment(request(amount=0.001))
    assert not result.executed and "minor-unit precision" in result.error
    assert not client.gateway.charges


def test_decimal_spend_does_not_reject_exact_budget_boundary():
    policy = PaymentPolicy(daily_budget=0.3, budget_warning_ratio=1, review_threshold=10, hard_limit=10)
    client = sdk(policy=policy)
    assert client.execute_payment(request(amount=0.1)).executed
    assert client.execute_payment(request(amount=0.2)).executed
    assert client.execution_ledger.spend_since("agent", 0) == Decimal("0.3")
    assert not client.execute_payment(request(amount=0.01)).executed


def test_legacy_subcent_history_is_preserved_without_authorizing_new_subcent_charges():
    ledger = SQLiteExecutionLedger()
    payment = request(amount=1.001)
    with pytest.raises(ValueError, match="minor-unit"):
        ledger.import_confirmed(payment, reference="historical-payment-1")
    imported = ledger.import_confirmed(payment, reference="historical-payment-1", allow_legacy_precision=True)
    assert imported.amount == "1.001" and imported.context["authorization"] == "historical_external_report"
    assert ledger.spend_since("agent", 0) == Decimal("1.001")
    assert ledger.import_confirmed(payment, reference="historical-payment-1", allow_legacy_precision=True) == imported
    assert len(ledger.pending_events()) == 1
    assert not sdk(ledger).execute_payment(request(amount=1.001)).executed


def test_claim_rechecks_budget_warning_band_after_concurrent_spend():
    class ConcurrentSpendLedger(SQLiteExecutionLedger):
        def claim(self, payment, **kwargs):
            # Another worker commits after the SDK evaluates its review but
            # before this atomic claim. The $100 cap is not exceeded, but the
            # 80% review threshold now is.
            self.import_confirmed(request(amount=30), reference="other-worker")
            return super().claim(payment, **kwargs)

    ledger, gateway = ConcurrentSpendLedger(), MockGateway()
    result = sdk(ledger, gateway).execute_payment(request(amount=60))
    assert not result.executed and result.error == "daily_budget_review_required"
    assert not gateway.charges


def test_review_expiring_while_claim_waits_never_reaches_gateway():
    now = [1_000]

    class DelayedLedger(SQLiteExecutionLedger):
        def claim(self, payment, **kwargs):
            claim = super().claim(payment, **kwargs)
            now[0] += 20
            return claim

    ledger, gateway = DelayedLedger(), MockGateway()
    client = sdk(ledger, gateway, clock=lambda: now[0], review_ttl_ms=10)
    payment = request()
    result = client.execute_payment(payment)
    assert not result.executed and result.error == "authorization_stale_before_submission"
    assert ledger.get(payment.request_id).status == "failed"
    assert not gateway.charges and ledger.spend_since("agent", 0) == 0


def test_mandate_expiring_while_claim_waits_never_reaches_gateway():
    now = [1_000]

    class DelayedLedger(SQLiteExecutionLedger):
        def claim(self, payment, **kwargs):
            claim = super().claim(payment, **kwargs)
            now[0] += 20
            return claim

    ledger, gateway = DelayedLedger(), MockGateway()
    client = sdk(ledger, gateway, clock=lambda: now[0], review_ttl_ms=900_000)
    payment = request(mandate=Mandate("human", "agent", expires_at_ms=1010))
    result = client.execute_payment(payment)
    assert not result.executed and result.error == "authorization_stale_before_submission"
    assert ledger.get(payment.request_id).status == "failed"
    assert not gateway.charges and ledger.spend_since("agent", 0) == 0
