"""Audit log read API (PZ-074)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from audit import AuditLog
from auth import require_audit_read
from deps import get_audit_log
from envelope import list_meta, ok

router = APIRouter(tags=["audit"])


@router.get("/v1/audit-logs")
def list_audit_logs(
    api_key: str = Depends(require_audit_read),
    actor: Optional[str] = Query(default=None),
    action: Optional[str] = Query(default=None),
    resource: Optional[str] = Query(default=None),
    from_ms: Optional[int] = Query(default=None, ge=0),
    to_ms: Optional[int] = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    audit: AuditLog = Depends(get_audit_log),
) -> dict[str, Any]:
    items, total = audit.list(actor, action, resource, from_ms, to_ms, limit, offset)
    return ok(items, meta=list_meta(total, limit, offset))
