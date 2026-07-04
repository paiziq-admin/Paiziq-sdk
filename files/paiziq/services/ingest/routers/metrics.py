"""Metrics endpoints for dashboard trends (PZ-079)."""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Query

from auth import require_read_key
from deps import get_metrics_store, get_org_store
from envelope import ApiError, ok
from stores.metrics import MetricsStore
from stores.orgs import OrgStore

router = APIRouter(tags=["metrics"])


@router.get("/v1/metrics/summary")
def metrics_summary(
    env_id: str = Query(min_length=1),
    from_ms: Optional[int] = Query(default=None, ge=0),
    to_ms: Optional[int] = Query(default=None, ge=0),
    api_key: str = Depends(require_read_key),
    metrics: MetricsStore = Depends(get_metrics_store),
    orgs: OrgStore = Depends(get_org_store),
) -> dict[str, Any]:
    if orgs.get_environment(env_id) is None:
        raise ApiError(404, "not_found", f"environment not found: {env_id}")
    return ok(metrics.summary(env_id, from_ms, to_ms))


@router.get("/v1/metrics/timeseries")
def metrics_timeseries(
    env_id: str = Query(min_length=1),
    metric: str = Query(min_length=1),
    interval: Literal["1h", "1d"] = Query(default="1h"),
    from_ms: Optional[int] = Query(default=None, ge=0),
    to_ms: Optional[int] = Query(default=None, ge=0),
    api_key: str = Depends(require_read_key),
    metrics: MetricsStore = Depends(get_metrics_store),
    orgs: OrgStore = Depends(get_org_store),
) -> dict[str, Any]:
    if orgs.get_environment(env_id) is None:
        raise ApiError(404, "not_found", f"environment not found: {env_id}")
    return ok(metrics.timeseries(env_id, metric, interval, from_ms, to_ms))
