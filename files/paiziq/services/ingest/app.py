"""Paiziq trace-ingest service (minimum viable cloud, build plan Week 3).

Endpoints (wire contract v1, see docs/02_ARCHITECTURE.md section 6):

    GET  /health                       liveness probe
    POST /v1/traces                    {"spans": [...]}  idempotent upsert
    POST /v1/notifications             notification webhook body
    GET  /v1/traces/{trace_id}         spans for one trace (dashboard/dev)
    GET  /v1/notifications             recent notifications (dashboard/dev)

Control-plane endpoints (envelope responses, docs/06_API_CONTRACT.md)
are mounted from routers/ (organizations, environments, ...).

Auth: per-customer API keys via the Authorization header
("Bearer <key>"). Keys come from the PAIZIQ_INGEST_KEYS env var
(comma-separated); the default "dev-key" is for local development only.

Run locally:
    pip3 install -r requirements.txt
    uvicorn app:app --reload --port 8800
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

import deps
from auth import require_api_key, settings
from envelope import install_error_handlers
from routers import agents, keys, orgs
from storage import IngestStore

app = FastAPI(title="Paiziq Ingest API", version="1.0")
install_error_handlers(app)
store = IngestStore(settings.database_path)
deps.init_stores(store.connection, store.lock)
app.include_router(orgs.router)
app.include_router(agents.router)
app.include_router(keys.router)


async def enforce_size_limit(request: Request) -> None:
    length = request.headers.get("content-length")
    if length and int(length) > settings.max_body_bytes:
        raise HTTPException(status_code=413, detail="Request body too large")


class SpanIn(BaseModel):
    name: str
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    duration_ms: Optional[int] = None
    status: str = "ok"
    attributes: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)


class TraceBatch(BaseModel):
    spans: list[SpanIn]


class NotificationIn(BaseModel):
    severity: str
    title: str
    message: str = ""
    request_id: Optional[str] = None
    risk_flags: list[str] = Field(default_factory=list)
    created_at_ms: Optional[int] = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/traces", dependencies=[Depends(enforce_size_limit)])
def ingest_traces(batch: TraceBatch, api_key: str = Depends(require_api_key)) -> dict[str, Any]:
    if len(batch.spans) > settings.max_spans_per_batch:
        raise HTTPException(status_code=413, detail="Too many spans in one batch")
    accepted = store.upsert_spans([s.model_dump() for s in batch.spans])
    return {"accepted": accepted}


@app.post("/v1/notifications", dependencies=[Depends(enforce_size_limit)])
def ingest_notification(
    notification: NotificationIn, api_key: str = Depends(require_api_key)
) -> dict[str, str]:
    store.add_notification(notification.model_dump())
    return {"status": "accepted"}


@app.get("/v1/traces/{trace_id}")
def get_trace(trace_id: str, api_key: str = Depends(require_api_key)) -> dict[str, Any]:
    return {"trace_id": trace_id, "spans": store.spans_for_trace(trace_id)}


@app.get("/v1/notifications")
def list_notifications(api_key: str = Depends(require_api_key)) -> dict[str, Any]:
    return {"notifications": store.notifications()}
