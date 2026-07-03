"""Safe failure modes (PZ-035): when the decision engine raises
unexpectedly, the SDK maps the failure to a deterministic verdict per
the configured FailureMode instead of raising into the agent."""

import pytest

from paiziq import DecisionStatus, FailureMode, PaiziqSDK, PaymentRequest


def req(**kw) -> PaymentRequest:
    defaults = dict(
        agent_id="agent-1",
        principal_id="user-1",
        merchant="acme.example",
        amount=25.0,
        currency="USD",
        intent_description="office supplies",
    )
    defaults.update(kw)
    return PaymentRequest(**defaults)


class BoomEngine:
    def evaluate(self, request):
        raise RuntimeError("policy store unavailable")


def sdk_with_broken_engine(mode: FailureMode) -> PaiziqSDK:
    sdk = PaiziqSDK(failure_mode=mode)
    sdk.engine = BoomEngine()
    return sdk


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (FailureMode.FAIL_OPEN, DecisionStatus.APPROVED),
        (FailureMode.FAIL_CLOSED, DecisionStatus.REJECTED),
        (FailureMode.REVIEW_REQUIRED, DecisionStatus.NEEDS_REVIEW),
    ],
)
def test_each_mode_maps_failure_to_expected_verdict(mode, expected):
    sdk = sdk_with_broken_engine(mode)
    decision = sdk.review_payment(req())
    assert decision.status is expected
    assert f"failure_mode:{mode.value}" in decision.reasons
    assert any("RuntimeError" in r for r in decision.reasons)


def test_default_mode_is_fail_closed():
    assert PaiziqSDK().failure_mode is FailureMode.FAIL_CLOSED
    sdk = PaiziqSDK()
    sdk.engine = BoomEngine()
    assert sdk.review_payment(req()).status is DecisionStatus.REJECTED


def test_failure_writes_audit_entry():
    sdk = sdk_with_broken_engine(FailureMode.REVIEW_REQUIRED)
    request = req()
    sdk.review_payment(request)
    trail = sdk.get_audit_trail(request_id=request.request_id)
    failure_entries = [
        r for r in trail if r["payload"].get("failure_mode") == "review_required"
    ]
    assert len(failure_entries) == 1
    payload = failure_entries[0]["payload"]
    assert payload["verdict"] == "needs_review"
    assert "RuntimeError" in payload["error"]


def test_fail_open_failure_can_still_execute():
    sdk = sdk_with_broken_engine(FailureMode.FAIL_OPEN)
    request = req()
    result = sdk.execute_payment(request)
    assert result.executed is True


def test_fail_closed_failure_blocks_execution():
    sdk = sdk_with_broken_engine(FailureMode.FAIL_CLOSED)
    result = sdk.execute_payment(req())
    assert result.executed is False


def test_review_required_failure_needs_human_approval():
    sdk = sdk_with_broken_engine(FailureMode.REVIEW_REQUIRED)
    request = req()
    decision = sdk.review_payment(request)
    assert decision.status is DecisionStatus.NEEDS_REVIEW
    blocked = sdk.execute_payment(request)
    assert blocked.executed is False
    sdk.approve_review(request.request_id, reviewer_id="rev-1")
    approved = sdk.execute_payment(request)
    assert approved.executed is True


def test_healthy_engine_is_unaffected_by_mode():
    sdk = PaiziqSDK(failure_mode=FailureMode.FAIL_OPEN)
    decision = sdk.review_payment(req(amount=25.0))
    assert decision.status is DecisionStatus.APPROVED
    assert not any(r.startswith("failure_mode:") for r in decision.reasons)


def test_string_mode_is_coerced_to_enum():
    assert PaiziqSDK(failure_mode="fail_open").failure_mode is FailureMode.FAIL_OPEN
    with pytest.raises(ValueError):
        PaiziqSDK(failure_mode="explode")
