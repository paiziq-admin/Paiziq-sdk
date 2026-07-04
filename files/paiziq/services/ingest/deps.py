"""FastAPI dependency wiring for control-plane stores."""

from __future__ import annotations

import sqlite3
import threading
from typing import Optional

from audit import AuditLog
from config import Settings
from event_router import EventRouter
from retention import RetentionJob
from stores.agents import AgentStore
from stores.decisions import DecisionStore, ReviewStore
from stores.keys import KeyStore
from stores.metrics import MetricsStore
from stores.orgs import OrgStore
from stores.payments import PaymentStore
from stores.policies import PolicyStore
from stores.webhooks import WebhookStore

_conn: Optional[sqlite3.Connection] = None
_lock: Optional[threading.Lock] = None
_org_store: Optional[OrgStore] = None
_agent_store: Optional[AgentStore] = None
_key_store: Optional[KeyStore] = None
_payment_store: Optional[PaymentStore] = None
_decision_store: Optional[DecisionStore] = None
_review_store: Optional[ReviewStore] = None
_policy_store: Optional[PolicyStore] = None
_webhook_store: Optional[WebhookStore] = None
_metrics_store: Optional[MetricsStore] = None
_event_router: Optional[EventRouter] = None
_retention_job: Optional[RetentionJob] = None
_audit_log: Optional[AuditLog] = None


def init_stores(conn: sqlite3.Connection, lock: threading.Lock, settings: Settings) -> None:
    global _conn, _lock, _org_store, _agent_store, _key_store, _payment_store, _audit_log
    global _decision_store, _review_store, _policy_store, _webhook_store, _metrics_store
    global _event_router, _retention_job
    _conn, _lock = conn, lock
    _org_store = OrgStore(conn, lock)
    _agent_store = AgentStore(conn, lock)
    _key_store = KeyStore(conn, lock)
    _payment_store = PaymentStore(conn, lock)
    _decision_store = DecisionStore(conn, lock)
    _review_store = ReviewStore(conn, lock)
    _policy_store = PolicyStore(conn, lock)
    _webhook_store = WebhookStore(conn, lock, settings.secrets_key)
    _metrics_store = MetricsStore(conn, lock)
    _event_router = EventRouter(_webhook_store, conn, lock, settings.review_sla_ms)
    _retention_job = RetentionJob(
        conn, lock,
        settings.retention_spans_days,
        settings.retention_notifications_days,
        settings.retention_audit_days,
    )
    _audit_log = AuditLog(conn, lock)


def get_db_connection() -> sqlite3.Connection:
    assert _conn is not None, "init_stores() not called"
    return _conn


def get_db_lock() -> threading.Lock:
    assert _lock is not None, "init_stores() not called"
    return _lock


def get_org_store() -> OrgStore:
    assert _org_store is not None, "init_stores() not called"
    return _org_store


def get_agent_store() -> AgentStore:
    assert _agent_store is not None, "init_stores() not called"
    return _agent_store


def get_key_store() -> KeyStore:
    assert _key_store is not None, "init_stores() not called"
    return _key_store


def get_payment_store() -> PaymentStore:
    assert _payment_store is not None, "init_stores() not called"
    return _payment_store


def get_decision_store() -> DecisionStore:
    assert _decision_store is not None, "init_stores() not called"
    return _decision_store


def get_review_store() -> ReviewStore:
    assert _review_store is not None, "init_stores() not called"
    return _review_store


def get_policy_store() -> PolicyStore:
    assert _policy_store is not None, "init_stores() not called"
    return _policy_store


def get_webhook_store() -> WebhookStore:
    assert _webhook_store is not None, "init_stores() not called"
    return _webhook_store


def get_metrics_store() -> MetricsStore:
    assert _metrics_store is not None, "init_stores() not called"
    return _metrics_store


def get_event_router() -> EventRouter:
    assert _event_router is not None, "init_stores() not called"
    return _event_router


def get_retention_job() -> RetentionJob:
    assert _retention_job is not None, "init_stores() not called"
    return _retention_job


def get_audit_log() -> AuditLog:
    assert _audit_log is not None, "init_stores() not called"
    return _audit_log
