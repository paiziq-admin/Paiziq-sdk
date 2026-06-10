"""Audit trail store and payment gateway abstraction.

AuditStore is append-only by contract. The in-memory and JSONL file
implementations cover dev/test; production deployments plug in a
Postgres-backed store via the same protocol.
"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Optional, Protocol

from ..models import AuditRecord, PaymentRequest
from .postgres import PostgresAuditStore

__all__ = [
    "AuditStore", "InMemoryAuditStore", "JSONLAuditStore", "PostgresAuditStore",
    "PaymentGateway", "MockGateway",
]


# ── Audit store ──────────────────────────────────────────────────────────────

class AuditStore(Protocol):
    def append(self, record: AuditRecord) -> None: ...
    def query(self, request_id: Optional[str] = None, limit: int = 100) -> list[AuditRecord]: ...


class InMemoryAuditStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[AuditRecord] = []

    def append(self, record: AuditRecord) -> None:
        with self._lock:
            self._records.append(record)

    def query(self, request_id: Optional[str] = None, limit: int = 100) -> list[AuditRecord]:
        with self._lock:
            records = [r for r in self._records if request_id is None or r.request_id == request_id]
        return records[-limit:]


class JSONLAuditStore:
    """Append-only JSON-lines file store. Durable across process restarts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, record: AuditRecord) -> None:
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.to_dict(), default=str) + "\n")

    def query(self, request_id: Optional[str] = None, limit: int = 100) -> list[AuditRecord]:
        if not self.path.exists():
            return []
        out: list[AuditRecord] = []
        with self._lock, self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if request_id is not None and d.get("request_id") != request_id:
                    continue
                out.append(
                    AuditRecord(
                        event_type=d["event_type"],
                        request_id=d["request_id"],
                        payload=d.get("payload", {}),
                        trace_id=d.get("trace_id"),
                        record_id=d.get("record_id", ""),
                        recorded_at_ms=d.get("recorded_at_ms", 0),
                    )
                )
        return out[-limit:]


# ── Payment gateway ──────────────────────────────────────────────────────────

class PaymentGateway(Protocol):
    name: str

    def charge(self, request: PaymentRequest) -> str:
        """Execute the payment; return a gateway reference. Raise on failure."""
        ...


class MockGateway:
    """Sandbox gateway for development and tests."""

    name = "mock"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.charges: list[PaymentRequest] = []

    def charge(self, request: PaymentRequest) -> str:
        if self.fail:
            raise RuntimeError("mock gateway declined the charge")
        self.charges.append(request)
        return f"mock_{uuid.uuid4().hex[:12]}"
