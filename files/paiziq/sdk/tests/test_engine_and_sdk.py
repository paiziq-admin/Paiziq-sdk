"""Engine aggregation, 4-way audit, and SDK end-to-end tests."""

import time

from paiziq import (
    DecisionStatus,
    Mandate,
    PaiziqSDK,
    PaymentPolicy,
    PaymentRequest,
    RiskFlag,
)
from paiziq.audit import MockGateway
from paiziq.engine.audit4 import FourWayAuditor, transaction_snapshot
from paiziq.engine.engine import DecisionEngine
from paiziq.models import AuditDimension
from paiziq.notifications import ConsoleNotifier
from paiziq.tracing.tracer import InMemoryExporter


def req(**kw) -> PaymentRequest:
    base = dict(agent_id="agent-1", principal_id="user-1", merchant="acme corp", amount=50.0)
    base.update(kw)
    return PaymentRequest(**base)


def make_sdk(policy=None, gateway=None):
    return PaiziqSDK(
        policy=policy or PaymentPolicy(review_threshold=100, hard_limit=1000),
        gateway=gateway or MockGateway(),
        exporters=[InMemoryExporter()],
        notifiers=[ConsoleNotifier()],
    )


# ── Engine aggregation ───────────────────────────────────────────────────────

def test_engine_returns_approved_with_reasons_and_flags():
    decision = DecisionEngine(PaymentPolicy()).evaluate(req(amount=10))
    assert decision.status is DecisionStatus.APPROVED
    assert decision.reasons == ["All decision rules passed"]
    assert decision.risk_flags == []
    assert len(decision.rule_results) == 6  # all default rules ran


def test_engine_rejected_outranks_review():
    policy = PaymentPolicy(
        hard_limit=100,                  # triggers rejected
        review_categories={"travel"},    # triggers needs_review
    )
    decision = DecisionEngine(policy).evaluate(req(amount=500, category="travel"))
    assert decision.status is DecisionStatus.REJECTED
    assert RiskFlag.OVER_HARD_LIMIT in decision.risk_flags
    assert RiskFlag.CATEGORY_REVIEW_REQUIRED in decision.risk_flags
    assert len(decision.reasons) >= 2  # both reasons preserved


def test_engine_serializes_to_dict():
    d = DecisionEngine(PaymentPolicy()).evaluate(req()).to_dict()
    assert d["status"] in {"approved", "needs_review", "rejected"}
    assert isinstance(d["reasons"], list) and isinstance(d["risk_flags"], list)
    assert {"rule", "status", "reasons"} <= set(d["rule_results"][0].keys())


def test_custom_rule_registration():
    class AlwaysReview:
        name = "always_review"

        def evaluate(self, request, policy):
            from paiziq.models import RuleResult
            return RuleResult(self.name, DecisionStatus.NEEDS_REVIEW, reasons=["custom rule"])

    engine = DecisionEngine(PaymentPolicy())
    engine.add_rule(AlwaysReview())
    assert engine.evaluate(req(amount=1)).status is DecisionStatus.NEEDS_REVIEW


# ── 4-way audit ──────────────────────────────────────────────────────────────

def mandate(**kw) -> Mandate:
    base = dict(principal_id="user-1", agent_id="agent-1", max_amount=200, currency="USD",
                allowed_merchants=["acme corp"])
    base.update(kw)
    return Mandate(**base)


def test_four_way_all_pass():
    r = req(mandate=mandate())
    engine = DecisionEngine(PaymentPolicy())
    decision = engine.evaluate(r)
    audit = FourWayAuditor().run(r, decision, transaction_snapshot(r))
    assert audit.passed
    assert {c.dimension for c in audit.checks} == set(AuditDimension)


def test_identity_mismatch_fails():
    r = req(mandate=mandate(principal_id="someone-else"))
    decision = DecisionEngine(PaymentPolicy()).evaluate(r)
    audit = FourWayAuditor().run(r, decision)
    assert AuditDimension.IDENTITY in audit.failed_dimensions


def test_intent_mismatch_amount_over_mandate():
    r = req(amount=999, mandate=mandate(max_amount=100))
    decision = DecisionEngine(PaymentPolicy(hard_limit=10_000, review_threshold=10_000)).evaluate(r)
    audit = FourWayAuditor().run(r, decision)
    assert AuditDimension.INTENT in audit.failed_dimensions


def test_intent_mismatch_expired_mandate():
    r = req(mandate=mandate(expires_at_ms=int(time.time() * 1000) - 1000))
    decision = DecisionEngine(PaymentPolicy()).evaluate(r)
    audit = FourWayAuditor().run(r, decision)
    assert AuditDimension.INTENT in audit.failed_dimensions


def test_transaction_tamper_detected():
    r = req(amount=50)
    decision = DecisionEngine(PaymentPolicy()).evaluate(r)
    snapshot = transaction_snapshot(r)
    r.amount = 5000  # tampered after review
    audit = FourWayAuditor().run(r, decision, snapshot)
    assert AuditDimension.TRANSACTION in audit.failed_dimensions


# ── SDK end-to-end ───────────────────────────────────────────────────────────

def test_happy_path_review_then_execute():
    gateway = MockGateway()
    sdk = make_sdk(gateway=gateway)
    r = req(amount=25)
    decision = sdk.review_payment(r)
    assert decision.status is DecisionStatus.APPROVED
    result = sdk.execute_payment(r)
    assert result.executed and result.gateway_reference
    assert len(gateway.charges) == 1


def test_execute_blocks_rejected_payment():
    gateway = MockGateway()
    sdk = make_sdk(gateway=gateway)
    r = req(amount=99_999)
    result = sdk.execute_payment(r)
    assert not result.executed
    assert "4-way audit failed" in (result.error or "")
    assert gateway.charges == []


def test_needs_review_blocks_until_human_approval():
    gateway = MockGateway()
    sdk = make_sdk(gateway=gateway)
    r = req(amount=500)  # above review threshold
    decision = sdk.review_payment(r)
    assert decision.status is DecisionStatus.NEEDS_REVIEW
    assert not sdk.execute_payment(r).executed

    sdk.approve_review(r.request_id, reviewer_id="cfo-1")
    result = sdk.execute_payment(r)
    assert result.executed
    assert len(gateway.charges) == 1


def test_audit_trail_records_full_lifecycle():
    sdk = make_sdk()
    r = req(amount=25)
    sdk.review_payment(r)
    sdk.execute_payment(r)
    trail = sdk.get_audit_trail(r.request_id)
    assert [e["event_type"] for e in trail] == ["review", "execution"]
    assert trail[0]["payload"]["status"] == "approved"
    assert trail[1]["payload"]["executed"] is True
    assert all(e["trace_id"] for e in trail)


def test_budget_commits_only_on_execution():
    sdk = make_sdk(policy=PaymentPolicy(daily_budget=100, review_threshold=1000, hard_limit=5000))
    a, b = req(amount=60), req(amount=60)
    sdk.review_payment(a)
    assert sdk.review_payment(b).status is DecisionStatus.APPROVED  # nothing spent yet
    sdk.execute_payment(a)
    c = req(amount=60)
    decision = sdk.review_payment(c)  # 60 spent + 60 pending > 100
    assert decision.status in (DecisionStatus.REJECTED, DecisionStatus.NEEDS_REVIEW)
    assert RiskFlag.BUDGET_EXCEEDED in decision.risk_flags


def test_gateway_failure_is_reported_not_raised():
    sdk = make_sdk(gateway=MockGateway(fail=True))
    result = sdk.execute_payment(req(amount=10))
    assert not result.executed and "declined" in (result.error or "")


def test_harmful_intent_triggers_critical_notification():
    notifier = ConsoleNotifier()
    sdk = PaiziqSDK(
        policy=PaymentPolicy(),
        exporters=[InMemoryExporter()],
        notifiers=[notifier],
    )
    sdk.review_payment(req(intent_description="split payments to avoid the limit"))
    assert notifier.sent and notifier.sent[0].severity == "critical"
    assert "Harmful intent" in notifier.sent[0].title
