"""Domain-model validation tests (PZ-028): PaymentRequest, Mandate, and
PaymentPolicy reject malformed input at construction with clear
ValueErrors; valid input is normalized (currency upper-cased)."""

from __future__ import annotations

import pytest

from paiziq import Mandate, PaymentPolicy, PaymentRequest


def req(**kw) -> PaymentRequest:
    base = dict(agent_id="agent-1", principal_id="user-1", merchant="acme corp", amount=50.0)
    base.update(kw)
    return PaymentRequest(**base)


# ── PaymentRequest ───────────────────────────────────────────────────────────

def test_valid_request_constructs_and_normalizes_currency():
    r = req(currency="usd")
    assert r.currency == "USD"


@pytest.mark.parametrize("amount", [0, -1, -0.01, float("nan"), float("inf"), "50", None, True])
def test_request_rejects_bad_amounts(amount):
    with pytest.raises(ValueError, match="PaymentRequest.amount"):
        req(amount=amount)


@pytest.mark.parametrize("currency", ["", "US", "DOLLARS", "U$D", "1SD", None])
def test_request_rejects_bad_currency(currency):
    with pytest.raises(ValueError, match="PaymentRequest.currency"):
        req(currency=currency)


@pytest.mark.parametrize("field_name", ["agent_id", "principal_id", "merchant"])
@pytest.mark.parametrize("bad", ["", "   ", None])
def test_request_rejects_empty_identifiers(field_name, bad):
    with pytest.raises(ValueError, match=f"PaymentRequest.{field_name}"):
        req(**{field_name: bad})


# ── Mandate ──────────────────────────────────────────────────────────────────

def test_valid_mandate_constructs():
    m = Mandate(principal_id="user-1", agent_id="agent-1", max_amount=200.0)
    assert m.mandate_id.startswith("mnd_")


def test_mandate_rejects_empty_ids_and_bad_amounts():
    with pytest.raises(ValueError, match="Mandate.principal_id"):
        Mandate(principal_id="", agent_id="agent-1")
    with pytest.raises(ValueError, match="Mandate.agent_id"):
        Mandate(principal_id="user-1", agent_id=" ")
    with pytest.raises(ValueError, match="Mandate.max_amount"):
        Mandate(principal_id="user-1", agent_id="agent-1", max_amount=-5)
    with pytest.raises(ValueError, match="Mandate.currency"):
        Mandate(principal_id="user-1", agent_id="agent-1", currency="EURO")


def test_mandate_without_max_amount_is_valid():
    assert Mandate(principal_id="user-1", agent_id="agent-1").max_amount is None


# ── PaymentPolicy ────────────────────────────────────────────────────────────

def test_default_policy_is_valid():
    PaymentPolicy()


@pytest.mark.parametrize(
    "kw",
    [
        {"review_threshold": 0},
        {"review_threshold": -10},
        {"hard_limit": -1},
        {"review_threshold": 500, "hard_limit": 100},  # inverted thresholds
        {"budget_warning_ratio": 0},
        {"budget_warning_ratio": 1.5},
        {"daily_budget": -100},
        {"monthly_budget": 0},
        {"max_tx_per_hour": 0},
        {"treat_unknown_merchant_as": "ignore"},
        {"allowed_currencies": {"US"}},
    ],
)
def test_policy_rejects_invalid_configuration(kw):
    with pytest.raises(ValueError):
        PaymentPolicy(**kw)


def test_policy_accepts_sane_configuration():
    PaymentPolicy(
        review_threshold=50,
        hard_limit=500,
        budget_warning_ratio=1.0,
        daily_budget=1000,
        monthly_budget=10000,
        max_tx_per_hour=10,
        allowed_currencies={"USD", "eur"},
        treat_unknown_merchant_as="rejected",
    )
