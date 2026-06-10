"""Phase 1 hardening tests, part 2: ScrubbingExporter behaviour,
property-based rule boundaries (hypothesis), and concurrency checks on
BudgetTracker / HTTPExporter."""

from __future__ import annotations

import threading

from hypothesis import given, settings
from hypothesis import strategies as st

from paiziq.engine.policy import BudgetTracker, InMemoryBudgetStore, PaymentPolicy
from paiziq.engine.rules import ThresholdRule
from paiziq.models import DecisionStatus, PaymentRequest
from paiziq.tracing.scrub import ScrubbingExporter
from paiziq.tracing.tracer import HTTPExporter, InMemoryExporter, Span

EMAIL = "jane" + chr(64) + "example.com"


def req(**kw) -> PaymentRequest:
    base = dict(agent_id="agent-1", principal_id="user-1", merchant="acme corp", amount=50.0)
    base.update(kw)
    return PaymentRequest(**base)


# ── ScrubbingExporter ────────────────────────────────────────────────────────

def test_scrubbing_exporter_wraps_inner():
    inner = InMemoryExporter()
    exporter = ScrubbingExporter(inner)
    exporter.export([Span(name="t", trace_id="tr", attributes={"note": f"mail {EMAIL}"})])
    assert inner.spans[0].attributes["note"] == "mail [REDACTED:email]"
    exporter.shutdown()


def test_scrubber_failure_never_raises():
    class Boom:
        def __call__(self, span):
            raise RuntimeError("boom")

    exporter = ScrubbingExporter(InMemoryExporter(), scrubber=Boom())
    exporter.export([Span(name="t", trace_id="tr")])  # must not raise


def test_sdk_end_to_end_with_scrubbing_exporter():
    from paiziq import PaiziqSDK

    inner = InMemoryExporter()
    sdk = PaiziqSDK(
        policy=PaymentPolicy(known_merchants={"acme corp"}),
        exporters=[ScrubbingExporter(inner)],
    )
    sdk.review_payment(req(intent_description=f"renew for {EMAIL}"))
    events = [e for s in inner.spans for e in s.events if e["name"] == "decision"]
    assert events
    assert EMAIL not in str(events)


# ── Property-based rule boundaries (hypothesis) ──────────────────────────────

@settings(max_examples=200, deadline=None)
@given(amount=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False))
def test_threshold_rule_total_and_consistent(amount):
    policy = PaymentPolicy(review_threshold=100.0, hard_limit=1000.0)
    result = ThresholdRule().evaluate(req(amount=amount), policy)
    if amount <= 0:
        assert result.status is DecisionStatus.REJECTED
    elif amount > policy.hard_limit:
        assert result.status is DecisionStatus.REJECTED
    elif amount > policy.review_threshold:
        assert result.status is DecisionStatus.NEEDS_REVIEW
    else:
        assert result.status is DecisionStatus.APPROVED
    assert result.reasons  # every verdict is explainable


@settings(max_examples=100, deadline=None)
@given(
    review=st.floats(min_value=0.01, max_value=1e5, allow_nan=False),
    spread=st.floats(min_value=0.01, max_value=1e5, allow_nan=False),
    amount=st.floats(min_value=0.01, max_value=2e5, allow_nan=False),
)
def test_threshold_severity_monotonic_in_policy(review, spread, amount):
    """A stricter policy can never produce a more permissive verdict."""
    severity = {
        DecisionStatus.APPROVED: 0,
        DecisionStatus.NEEDS_REVIEW: 1,
        DecisionStatus.REJECTED: 2,
    }
    loose = PaymentPolicy(review_threshold=review + spread, hard_limit=review + 2 * spread)
    strict = PaymentPolicy(review_threshold=review, hard_limit=review + spread)
    rule = ThresholdRule()
    loose_status = rule.evaluate(req(amount=amount), loose).status
    strict_status = rule.evaluate(req(amount=amount), strict).status
    assert severity[strict_status] >= severity[loose_status]


# ── Concurrency ──────────────────────────────────────────────────────────────

def test_budget_tracker_concurrent_commits():
    tracker = BudgetTracker(store=InMemoryBudgetStore())
    n_threads, per_thread = 8, 50

    def worker():
        for _ in range(per_thread):
            tracker.commit("agent-1", 1.0)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert tracker.daily_spend("agent-1") == n_threads * per_thread
    assert tracker.hourly_tx_count("agent-1") == n_threads * per_thread


def test_http_exporter_concurrent_export_never_raises():
    # Unroutable endpoint: every send fails; export must stay silent.
    exporter = HTTPExporter(
        "http://127.0.0.1:9", api_key="test-key", batch_size=10,
        flush_interval_s=0.05, max_retries=1, timeout_s=0.1,
    )

    def worker():
        for i in range(100):
            exporter.export([Span(name=f"s{i}", trace_id="tr")])

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    exporter.shutdown()  # must not raise or hang


def test_http_exporter_drops_under_backpressure():
    exporter = HTTPExporter(
        "http://127.0.0.1:9", api_key="test-key",
        flush_interval_s=10.0, max_retries=1, timeout_s=0.1,
    )
    exporter._q.maxsize = 5  # force the bounded-queue drop path
    for i in range(50):
        exporter.export([Span(name=f"s{i}", trace_id="tr")])
    exporter.shutdown()
