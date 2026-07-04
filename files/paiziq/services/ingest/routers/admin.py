"""Admin maintenance endpoints (PZ-081)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from audit import AuditLog
from auth import actor_for, require_admin_key
from deps import get_audit_log, get_retention_job
from envelope import ok
from retention import RetentionJob

router = APIRouter(tags=["admin"])


@router.post("/v1/admin/retention/run")
def run_retention(
    api_key: str = Depends(require_admin_key),
    job: RetentionJob = Depends(get_retention_job),
    audit: AuditLog = Depends(get_audit_log),
) -> dict[str, Any]:
    deleted = job.run()
    audit.record(actor_for(api_key), "retention.run", "system", deleted)
    return ok(deleted)
