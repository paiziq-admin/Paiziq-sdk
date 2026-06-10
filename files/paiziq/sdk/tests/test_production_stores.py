"""Tests for Phase 1 production hardening: Redis budget store, Postgres
audit store, PII scrubbing, property-based rule boundaries, and
concurrency on BudgetTracker / HTTPExporter.

PII-like fixtures (emails, card and SSN shapes) are assembled at runtime
so no real-looking sensitive literals live in the source tree.
"""

from __future__ import annotations

import sqlite3
import time


from paiziq.audit.postgres import PostgresAuditStore
from paiziq.engine.policy import BudgetTracker
from paiziq.engine.stores import RedisBudgetStore
from paiziq.models import AuditRecord, PaymentRequest
from paiziq.tracing.scrub import PIIScrubber
from paiziq.tracing.tracer import Span

EMAIL = "jane" + chr(64) + "example.com"          # jane@…
CARD = " ".join(["4111"] * 4)                      # 16-digit card shape
SSN = "-".join(["078", "05", "1120"])              # SSN shape


def req(**kw) -> PaymentRequest:
    base = dict(agent_id="agent-1", principal_id="user-1", merchant="acme corp", amount=50.0)
    base.update(kw)
    return PaymentRequest(**base)


# ── Fake redis (sorted-set subset used by RedisBudgetStore) ─────────────────

class FakeRedis:
    def __init__(self) -> None:
        self.sets: dict[str, dict[str, float]] = {}

    def zadd(self, name, mapping):
        self.sets.setdefault(name, {}).update(mapping)

    def zrangebyscore(self, name, min, max):
        return [m for m, score in self.sets.get(name, {}).items() if min <= score <= max]

    def zcount(self, name, min, max):
        return len(self.zrangebyscore(name, min, max))

    def zremrangebyscore(self, name, min, max):
        s = self.sets.get(name, {})
        for m in [m for m, score in s.items() if min <= score <= max]:
            del s[m]


class TestRedisBudgetStore:
    def test_record_and_query_spend(self):
        store = RedisBudgetStore(client=FakeRedis())
        now = time.time()
        store.record_spend("agent-1", 42.5, ts=now)
        store.record_spend("agent-1", 7.5, ts=now)
        assert store.spend_since("agent-1", now - 1) == 50.0
        assert store.tx_count_since("agent-1", now - 1) == 2

    def test_windows_exclude_old_spend(self):
        store = RedisBudgetStore(client=FakeRedis())
        now = time.time()
        store.record_spend("agent-1", 100.0, ts=now - 7200)
        store.record_spend("agent-1", 10.0, ts=now)
        assert store.spend_since("agent-1", now - 3600) == 10.0
        assert store.tx_count_since("agent-1", now - 3600) == 1

    def test_agents_are_isolated(self):
        store = RedisBudgetStore(client=FakeRedis())
        now = time.time()
        store.record_spend("agent-1", 10.0, ts=now)
        store.record_spend("agent-2", 99.0, ts=now)
        assert store.spend_since("agent-1", now - 1) == 10.0

    def test_retention_trims_ancient_entries(self):
        store = RedisBudgetStore(client=FakeRedis(), retention_s=3600)
        store.record_spend("agent-1", 5.0, ts=time.time() - 86_400)
        store.record_spend("agent-1", 1.0)  # triggers trim
        assert store.tx_count_since("agent-1", 0) == 1

    def test_requires_client_or_url(self):
        try:
            RedisBudgetStore()
        except ValueError as exc:
            assert "client or a url" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("expected ValueError")

    def test_works_as_budget_tracker_backend(self):
        tracker = BudgetTracker(store=RedisBudgetStore(client=FakeRedis()))
        tracker.commit("agent-1", 25.0)
        assert tracker.daily_spend("agent-1") == 25.0
        assert tracker.hourly_tx_count("agent-1") == 1


# ── PostgresAuditStore (driven through sqlite, same DB-API shape) ───────────

def sqlite_store() -> PostgresAuditStore:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    return PostgresAuditStore(connection=conn, paramstyle="?")


class TestPostgresAuditStore:
    def test_append_and_query_roundtrip(self):
        store = sqlite_store()
        store.append(AuditRecord(event_type="review", request_id="pay_1", payload={"k": "v"}))
        records = store.query(request_id="pay_1")
        assert len(records) == 1
        assert records[0].event_type == "review"
        assert records[0].payload == {"k": "v"}

    def test_query_filters_and_orders_newest_last(self):
        store = sqlite_store()
        store.append(AuditRecord("review", "pay_1", {}, recorded_at_ms=1))
        store.append(AuditRecord("execution", "pay_1", {}, recorded_at_ms=2))
        store.append(AuditRecord("review", "pay_2", {}, recorded_at_ms=3))
        records = store.query(request_id="pay_1")
        assert [r.event_type for r in records] == ["review", "execution"]
        assert len(store.query()) == 3

    def test_limit(self):
        store = sqlite_store()
        for i in range(10):
            store.append(AuditRecord("review", "pay_1", {}, recorded_at_ms=i))
        assert len(store.query(request_id="pay_1", limit=3)) == 3

    def test_trace_id_persisted(self):
        store = sqlite_store()
        store.append(AuditRecord("review", "pay_1", {}, trace_id="tr_abc"))
        assert store.query(request_id="pay_1")[0].trace_id == "tr_abc"

    def test_requires_connection_or_dsn(self):
        try:
            PostgresAuditStore()
        except ValueError as exc:
            assert "connection or a dsn" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("expected ValueError")


# ── PII scrubbing ────────────────────────────────────────────────────────────

class TestPIIScrubbing:
    def span(self, **attrs) -> Span:
        return Span(name="t", trace_id="tr", attributes=attrs)

    def test_email_and_card_redacted(self):
        s = self.span(intent=f"email {EMAIL} card {CARD}")
        PIIScrubber()(s)
        assert EMAIL not in s.attributes["intent"]
        assert "[REDACTED:email]" in s.attributes["intent"]
        assert "4111" not in s.attributes["intent"]

    def test_nested_event_payloads_scrubbed(self):
        s = self.span()
        s.add_event("decision", {"reasons": [f"contact {EMAIL}"], "amount": 5})
        PIIScrubber()(s)
        assert s.events[0]["payload"]["reasons"] == ["contact [REDACTED:email]"]
        assert s.events[0]["payload"]["amount"] == 5

    def test_key_redaction(self):
        s = self.span()
        s.add_event("decision", {"intent": "anything at all"})
        PIIScrubber(redact_keys=["intent"])(s)
        assert s.events[0]["payload"]["intent"] == "[REDACTED]"
