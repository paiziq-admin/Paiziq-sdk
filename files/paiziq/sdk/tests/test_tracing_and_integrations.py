"""Tracer, exporter, and integration tests."""

import pytest

from paiziq import (
    DecisionStatus,
    PaiziqSDK,
    PaymentBlockedError,
    PaymentPolicy,
    PaymentRequest,
    instrument_payment_tool,
    guard_tool_call,
)
from paiziq.notifications import ConsoleNotifier
from paiziq.tracing.tracer import InMemoryExporter, Tracer


def make_sdk(exporter=None, **policy_kw):
    return PaiziqSDK(
        policy=PaymentPolicy(**policy_kw) if policy_kw else PaymentPolicy(),
        exporters=[exporter or InMemoryExporter()],
        notifiers=[ConsoleNotifier()],
    )


def req(**kw) -> PaymentRequest:
    base = dict(agent_id="agent-1", principal_id="user-1", merchant="acme corp", amount=10.0)
    base.update(kw)
    return PaymentRequest(**base)


# ── Tracer ───────────────────────────────────────────────────────────────────

def test_span_lifecycle_and_export():
    exp = InMemoryExporter()
    tracer = Tracer([exp], service_name="test-agent")
    with tracer.span("outer", {"k": "v"}) as outer:
        outer.add_event("hello", {"x": 1})
        with tracer.span("inner"):
            pass
    assert len(exp.spans) == 2
    inner_span = next(s for s in exp.spans if s.name == "inner")
    outer_span = next(s for s in exp.spans if s.name == "outer")
    assert inner_span.parent_span_id == outer_span.span_id
    assert inner_span.trace_id == outer_span.trace_id
    assert outer_span.status == "ok" and outer_span.end_ms is not None
    assert outer_span.attributes["service.name"] == "test-agent"


def test_span_marks_error_and_reraises():
    exp = InMemoryExporter()
    tracer = Tracer([exp])
    with pytest.raises(ValueError):
        with tracer.span("boom"):
            raise ValueError("x")
    assert exp.spans[0].status == "error"


def test_exporter_failure_does_not_break_agent():
    class Broken:
        def export(self, spans):
            raise RuntimeError("down")

        def shutdown(self):
            pass

    tracer = Tracer([Broken()])
    with tracer.span("ok"):
        pass  # must not raise


def test_review_emits_decision_span():
    exp = InMemoryExporter()
    sdk = make_sdk(exporter=exp)
    sdk.review_payment(req())
    span = next(s for s in exp.spans if s.name == "paiziq.review_payment")
    assert span.attributes["paiziq.decision"] == "approved"
    assert any(e["name"] == "decision" for e in span.events)


# ── Generic decorator integration ────────────────────────────────────────────

def test_instrument_blocks_disallowed_payment():
    sdk = make_sdk(hard_limit=100)
    calls = []

    @instrument_payment_tool(sdk, extract=lambda merchant, amount: req(merchant=merchant, amount=amount))
    def pay(merchant: str, amount: float):
        calls.append((merchant, amount))
        return "charged"

    assert pay("acme corp", 10) == "charged"
    with pytest.raises(PaymentBlockedError) as ei:
        pay("acme corp", 10_000)
    assert ei.value.decision.status is DecisionStatus.REJECTED
    assert calls == [("acme corp", 10)]


# ── OpenAI tool-call guard ───────────────────────────────────────────────────

def test_guard_tool_call_passes_non_payment_tools():
    sdk = make_sdk()
    assert guard_tool_call(sdk, "search_web", '{"q": "news"}') is None


def test_guard_tool_call_blocks_payment_over_limit():
    sdk = make_sdk(hard_limit=100)
    with pytest.raises(PaymentBlockedError):
        guard_tool_call(
            sdk, "execute_payment", '{"merchant": "acme corp", "amount": 9999}'
        )


def test_guard_tool_call_approves_valid_payment():
    sdk = make_sdk()
    decision = guard_tool_call(
        sdk, "execute_payment", {"merchant": "acme corp", "amount": 5, "intent": "subscription"}
    )
    assert decision is not None and decision.status is DecisionStatus.APPROVED
