"""Unit tests for individual decision rules."""

import time

from paiziq.engine.policy import BudgetTracker, PaymentPolicy
from paiziq.engine.rules import (
    BudgetRule,
    HarmfulIntentRule,
    MerchantListRule,
    ReviewRequiredRule,
    ThresholdRule,
    UnknownMerchantRule,
)
from paiziq.models import DecisionStatus, PaymentRequest, RiskFlag


def req(**kw) -> PaymentRequest:
    base = dict(agent_id="agent-1", principal_id="user-1", merchant="acme corp", amount=50.0)
    base.update(kw)
    return PaymentRequest(**base)


# ── Threshold checks ─────────────────────────────────────────────────────────

def test_threshold_approves_small_amount():
    r = ThresholdRule().evaluate(req(amount=50), PaymentPolicy(review_threshold=100, hard_limit=1000))
    assert r.status is DecisionStatus.APPROVED


def test_threshold_flags_review_between_thresholds():
    r = ThresholdRule().evaluate(req(amount=500), PaymentPolicy(review_threshold=100, hard_limit=1000))
    assert r.status is DecisionStatus.NEEDS_REVIEW
    assert RiskFlag.OVER_REVIEW_THRESHOLD in r.risk_flags
    assert any("review threshold" in x for x in r.reasons)


def test_threshold_rejects_over_hard_limit():
    r = ThresholdRule().evaluate(req(amount=5000), PaymentPolicy(hard_limit=1000))
    assert r.status is DecisionStatus.REJECTED
    assert RiskFlag.OVER_HARD_LIMIT in r.risk_flags


def test_threshold_rejects_non_positive_amount():
    r = ThresholdRule().evaluate(req(amount=0), PaymentPolicy())
    assert r.status is DecisionStatus.REJECTED


# ── Merchant allowlist / blocklist ──────────────────────────────────────────

def test_blocklist_rejects():
    policy = PaymentPolicy(merchant_blocklist={"Shady LLC"})
    r = MerchantListRule().evaluate(req(merchant="shady llc"), policy)
    assert r.status is DecisionStatus.REJECTED
    assert RiskFlag.MERCHANT_BLOCKED in r.risk_flags


def test_allowlist_rejects_off_list_merchant():
    policy = PaymentPolicy(merchant_allowlist={"Acme Corp"})
    r = MerchantListRule().evaluate(req(merchant="Other Inc"), policy)
    assert r.status is DecisionStatus.REJECTED
    assert RiskFlag.MERCHANT_NOT_ALLOWLISTED in r.risk_flags


def test_allowlist_passes_listed_merchant_case_insensitive():
    policy = PaymentPolicy(merchant_allowlist={"ACME CORP"})
    r = MerchantListRule().evaluate(req(merchant="acme corp"), policy)
    assert r.status is DecisionStatus.APPROVED


# ── Unknown merchant detection ───────────────────────────────────────────────

def test_unknown_merchant_needs_review():
    policy = PaymentPolicy(known_merchants={"acme corp"})
    r = UnknownMerchantRule().evaluate(req(merchant="never seen llc"), policy)
    assert r.status is DecisionStatus.NEEDS_REVIEW
    assert RiskFlag.UNKNOWN_MERCHANT in r.risk_flags


def test_unknown_merchant_can_be_rejected_per_policy():
    policy = PaymentPolicy(known_merchants={"acme corp"}, treat_unknown_merchant_as="rejected")
    r = UnknownMerchantRule().evaluate(req(merchant="never seen llc"), policy)
    assert r.status is DecisionStatus.REJECTED


def test_known_merchant_passes():
    policy = PaymentPolicy(known_merchants={"acme corp"})
    r = UnknownMerchantRule().evaluate(req(merchant="Acme Corp"), policy)
    assert r.status is DecisionStatus.APPROVED


def test_unknown_merchant_detection_disabled_without_known_set():
    r = UnknownMerchantRule().evaluate(req(merchant="whatever"), PaymentPolicy())
    assert r.status is DecisionStatus.APPROVED


# ── Budget validation ────────────────────────────────────────────────────────

def test_budget_rejects_when_exceeded():
    tracker = BudgetTracker()
    tracker.commit("agent-1", 90)
    rule = BudgetRule(tracker)
    r = rule.evaluate(req(amount=20), PaymentPolicy(daily_budget=100))
    assert r.status is DecisionStatus.REJECTED
    assert RiskFlag.BUDGET_EXCEEDED in r.risk_flags


def test_budget_warns_near_limit():
    tracker = BudgetTracker()
    tracker.commit("agent-1", 70)
    rule = BudgetRule(tracker)
    r = rule.evaluate(req(amount=15), PaymentPolicy(daily_budget=100, budget_warning_ratio=0.8))
    assert r.status is DecisionStatus.NEEDS_REVIEW
    assert RiskFlag.BUDGET_NEAR_LIMIT in r.risk_flags


def test_budget_passes_within_limits():
    rule = BudgetRule(BudgetTracker())
    r = rule.evaluate(req(amount=10), PaymentPolicy(daily_budget=100, monthly_budget=1000))
    assert r.status is DecisionStatus.APPROVED


def test_monthly_budget_enforced():
    tracker = BudgetTracker()
    tracker.store.record_spend("agent-1", 950, ts=time.time() - 5 * 86400)
    rule = BudgetRule(tracker)
    r = rule.evaluate(req(amount=100), PaymentPolicy(monthly_budget=1000))
    assert r.status is DecisionStatus.REJECTED


# ── Review-required logic ────────────────────────────────────────────────────

def test_review_category_forces_review():
    policy = PaymentPolicy(review_categories={"gift_cards"})
    r = ReviewRequiredRule().evaluate(req(category="Gift_Cards"), policy)
    assert r.status is DecisionStatus.NEEDS_REVIEW
    assert RiskFlag.CATEGORY_REVIEW_REQUIRED in r.risk_flags


def test_disallowed_currency_forces_review():
    policy = PaymentPolicy(allowed_currencies={"USD"})
    r = ReviewRequiredRule().evaluate(req(currency="EUR"), policy)
    assert r.status is DecisionStatus.NEEDS_REVIEW
    assert RiskFlag.CURRENCY_NOT_PERMITTED in r.risk_flags


def test_velocity_anomaly_forces_review():
    tracker = BudgetTracker()
    for _ in range(5):
        tracker.commit("agent-1", 1)
    rule = ReviewRequiredRule(tracker)
    r = rule.evaluate(req(amount=5), PaymentPolicy(max_tx_per_hour=5))
    assert r.status is DecisionStatus.NEEDS_REVIEW
    assert RiskFlag.VELOCITY_ANOMALY in r.risk_flags


# ── Harmful intent ───────────────────────────────────────────────────────────

def test_harmful_intent_flagged():
    r = HarmfulIntentRule().evaluate(
        req(intent_description="buy gift cards in bulk to avoid detection"), PaymentPolicy()
    )
    assert r.status is DecisionStatus.NEEDS_REVIEW
    assert RiskFlag.HARMFUL_INTENT_SUSPECTED in r.risk_flags


def test_benign_intent_passes():
    r = HarmfulIntentRule().evaluate(
        req(intent_description="renew the team's monthly SaaS subscription"), PaymentPolicy()
    )
    assert r.status is DecisionStatus.APPROVED
