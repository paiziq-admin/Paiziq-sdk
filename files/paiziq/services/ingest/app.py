"""Paiziq trace-ingest service."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

import deps
from audit import AuditLog
from auth import actor_for, require_api_key, settings
from envelope import ApiError, install_error_handlers
from rate_limit import RateLimiter
from routers import admin, agents, audit, decisions, keys, metrics, orgs, payments, policies, search, webhooks
from storage import IngestStore
from webhook_worker import worker_loop

_rate_limiter = RateLimiter(settings.rate_limit_rpm)
_worker_stop: Optional[asyncio.Event] = None
_worker_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _worker_stop, _worker_task
    _worker_stop = asyncio.Event()
    _worker_task = asyncio.create_task(
        worker_loop(
            deps.get_webhook_store(),
            deps.get_event_router(),
            deps.get_db_connection(),
            deps.get_db_lock(),
            deps.get_retention_job(),
            settings.worker_interval_s,
            _worker_stop,
        )
    )
    yield
    assert _worker_stop is not None
    _worker_stop.set()
    if _worker_task is not None:
        await _worker_task


app = FastAPI(title="Paiziq Ingest API", version="1.0", lifespan=lifespan)
install_error_handlers(app)
store = IngestStore(settings.database_path)
deps.init_stores(store.connection, store.lock, settings)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(settings.cors_origins),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    key = auth.split(" ", 1)[1][:12] if auth.lower().startswith("bearer ") else request.client.host if request.client else "anon"
    if not _rate_limiter.allow(key):
        return JSONResponse(
            status_code=429,
            content={"success": False, "data": None,
                     "error": {"code": "rate_limited", "message": "Too many requests"}},
        )
    return await call_next(request)


app.include_router(orgs.router)
app.include_router(agents.router)
app.include_router(keys.router)
app.include_router(payments.router)
app.include_router(decisions.router)
app.include_router(policies.router)
app.include_router(webhooks.router)
app.include_router(metrics.router)
app.include_router(search.router)
app.include_router(audit.router)
app.include_router(admin.router)


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
def ingest_traces(
    batch: TraceBatch,
    api_key: str = Depends(require_api_key),
    audit: AuditLog = Depends(deps.get_audit_log),
) -> dict[str, Any]:
    if len(batch.spans) > settings.max_spans_per_batch:
        raise HTTPException(status_code=413, detail="Too many spans in one batch")
    accepted = store.upsert_spans([s.model_dump() for s in batch.spans])
    audit.record(actor_for(api_key), "trace.ingest", batch.spans[0].trace_id if batch.spans else "none",
                 {"accepted": accepted})
    return {"accepted": accepted}


@app.post("/v1/notifications", dependencies=[Depends(enforce_size_limit)])
def ingest_notification(
    notification: NotificationIn,
    api_key: str = Depends(require_api_key),
    audit: AuditLog = Depends(deps.get_audit_log),
) -> dict[str, str]:
    store.add_notification(notification.model_dump())
    audit.record(actor_for(api_key), "notification.ingest", notification.request_id or "none",
                 {"severity": notification.severity, "title": notification.title})
    return {"status": "accepted"}


@app.get("/v1/traces/{trace_id}")
def get_trace(trace_id: str, api_key: str = Depends(require_api_key)) -> dict[str, Any]:
    return {"trace_id": trace_id, "spans": store.spans_for_trace(trace_id)}


@app.get("/v1/notifications")
def list_notifications(api_key: str = Depends(require_api_key)) -> dict[str, Any]:
    return {"notifications": store.notifications()}
