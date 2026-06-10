"""Paiziq tracing layer.

OpenTelemetry-inspired but dependency-free. A Tracer produces Spans;
Spans are flushed to one or more Exporters. The HTTPExporter batches
spans on a background thread and ships them to the Paiziq Admin
Dashboard ingest endpoint with retry + backoff. Export failures NEVER
raise into the host agent — observability must not break payments.
"""

from __future__ import annotations

import atexit
import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

logger = logging.getLogger("paiziq.tracing")


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Span:
    name: str
    trace_id: str
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    parent_span_id: Optional[str] = None
    start_ms: int = field(default_factory=_now_ms)
    end_ms: Optional[int] = None
    status: str = "ok"  # ok | error
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def add_event(self, name: str, payload: Optional[dict[str, Any]] = None) -> None:
        self.events.append({"name": name, "ts_ms": _now_ms(), "payload": payload or {}})

    def end(self, status: str = "ok") -> None:
        self.end_ms = _now_ms()
        self.status = status

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "duration_ms": (self.end_ms - self.start_ms) if self.end_ms else None,
            "status": self.status,
            "attributes": self.attributes,
            "events": self.events,
        }


class Exporter(Protocol):
    def export(self, spans: list[Span]) -> None: ...
    def shutdown(self) -> None: ...


class InMemoryExporter:
    """Collects spans in memory. Used in tests and local debugging."""

    def __init__(self) -> None:
        self.spans: list[Span] = []

    def export(self, spans: list[Span]) -> None:
        self.spans.extend(spans)

    def shutdown(self) -> None:  # pragma: no cover - nothing to do
        pass


class ConsoleExporter:
    """Pretty-prints spans to the local logger for development."""

    def export(self, spans: list[Span]) -> None:
        for s in spans:
            logger.info("paiziq.span %s", json.dumps(s.to_dict(), default=str))

    def shutdown(self) -> None:  # pragma: no cover
        pass


class HTTPExporter:
    """Batched, retrying exporter to the Paiziq dashboard ingest API.

    POST {endpoint}/v1/traces  with  {"api_key_hint": ..., "spans": [...]}
    Authentication: Bearer token header. Runs on a daemon thread.
    """

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        batch_size: int = 50,
        flush_interval_s: float = 2.0,
        max_retries: int = 3,
        timeout_s: float = 5.0,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.batch_size = batch_size
        self.flush_interval_s = flush_interval_s
        self.max_retries = max_retries
        self.timeout_s = timeout_s
        self._q: queue.Queue[Optional[Span]] = queue.Queue(maxsize=10_000)
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run, name="paiziq-exporter", daemon=True)
        self._worker.start()
        atexit.register(self.shutdown)

    def export(self, spans: list[Span]) -> None:
        for s in spans:
            try:
                self._q.put_nowait(s)
            except queue.Full:  # drop rather than block the agent
                logger.warning("paiziq trace queue full; dropping span %s", s.name)

    def _run(self) -> None:
        batch: list[Span] = []
        last_flush = time.time()
        while not self._stop.is_set() or not self._q.empty():
            timeout = max(0.05, self.flush_interval_s - (time.time() - last_flush))
            try:
                item = self._q.get(timeout=timeout)
                if item is not None:
                    batch.append(item)
            except queue.Empty:
                pass
            if batch and (len(batch) >= self.batch_size or time.time() - last_flush >= self.flush_interval_s):
                self._send(batch)
                batch = []
                last_flush = time.time()
        if batch:
            self._send(batch)

    def _send(self, batch: list[Span]) -> None:
        body = json.dumps({"spans": [s.to_dict() for s in batch]}, default=str).encode()
        req = urllib.request.Request(
            f"{self.endpoint}/v1/traces",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "paiziq-sdk/0.1.0",
            },
        )
        delay = 0.5
        for attempt in range(1, self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s):
                    return
            except (urllib.error.URLError, OSError) as exc:
                logger.warning("paiziq export attempt %d failed: %s", attempt, exc)
                if attempt == self.max_retries:
                    logger.error("paiziq export gave up on batch of %d spans", len(batch))
                    return
                time.sleep(delay)
                delay *= 2

    def shutdown(self) -> None:
        self._stop.set()
        if self._worker.is_alive():
            self._worker.join(timeout=self.flush_interval_s + self.timeout_s)


class Tracer:
    """Creates spans and routes them to exporters. Thread-safe."""

    def __init__(self, exporters: Optional[list[Exporter]] = None, service_name: str = "payment-agent") -> None:
        self.exporters: list[Exporter] = exporters or [ConsoleExporter()]
        self.service_name = service_name
        self._local = threading.local()

    # ── trace context ────────────────────────────────────────────────
    def current_trace_id(self) -> str:
        tid = getattr(self._local, "trace_id", None)
        if tid is None:
            tid = uuid.uuid4().hex
            self._local.trace_id = tid
        return tid

    def new_trace(self) -> str:
        self._local.trace_id = uuid.uuid4().hex
        self._local.parent_span_id = None
        return self._local.trace_id

    # ── spans ────────────────────────────────────────────────────────
    @contextmanager
    def span(self, name: str, attributes: Optional[dict[str, Any]] = None):
        s = Span(
            name=name,
            trace_id=self.current_trace_id(),
            parent_span_id=getattr(self._local, "parent_span_id", None),
            attributes={"service.name": self.service_name, **(attributes or {})},
        )
        prev_parent = getattr(self._local, "parent_span_id", None)
        self._local.parent_span_id = s.span_id
        try:
            yield s
            s.end("ok")
        except Exception:
            s.end("error")
            raise
        finally:
            self._local.parent_span_id = prev_parent
            self._export([s])

    def emit(self, span: Span) -> None:
        self._export([span])

    def _export(self, spans: list[Span]) -> None:
        for exp in self.exporters:
            try:
                exp.export(spans)
            except Exception:  # never propagate observability failures
                logger.exception("paiziq exporter %s failed", type(exp).__name__)

    def shutdown(self) -> None:
        for exp in self.exporters:
            try:
                exp.shutdown()
            except Exception:
                logger.exception("paiziq exporter shutdown failed")
