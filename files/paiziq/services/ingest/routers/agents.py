"""Agent registration and metadata endpoints (contract §5)."""

from __future__ import annotations

import sqlite3
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from audit import AuditLog
from auth import actor_for, require_api_key
from deps import get_agent_store, get_audit_log, get_org_store
from envelope import ApiError, list_meta, ok
from stores.agents import AgentStore
from stores.orgs import OrgStore

router = APIRouter(tags=["agents"])


class AgentRegister(BaseModel):
    env_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    framework: Optional[str] = Field(default=None, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    status: Optional[Literal["active", "disabled"]] = None
    metadata: Optional[dict[str, Any]] = None


@router.post("/v1/agents")
def register_agent(
    body: AgentRegister,
    api_key: str = Depends(require_api_key),
    agents: AgentStore = Depends(get_agent_store),
    orgs: OrgStore = Depends(get_org_store),
    audit: AuditLog = Depends(get_audit_log),
) -> dict[str, Any]:
    if orgs.get_environment(body.env_id) is None:
        raise ApiError(404, "not_found", f"environment not found: {body.env_id}")
    agent, created = agents.register(
        body.env_id, body.name.strip(), body.framework, body.metadata
    )
    if created:
        audit.record(
            actor_for(api_key), "agent.register", agent["id"],
            {"env_id": agent["env_id"], "name": agent["name"]},
        )
    return ok(agent)


@router.get("/v1/agents")
def list_agents(
    api_key: str = Depends(require_api_key),
    env_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    agents: AgentStore = Depends(get_agent_store),
) -> dict[str, Any]:
    items, total = agents.list(env_id, limit, offset)
    return ok(items, meta=list_meta(total, limit, offset))


@router.get("/v1/agents/{agent_id}")
def get_agent(
    agent_id: str,
    api_key: str = Depends(require_api_key),
    agents: AgentStore = Depends(get_agent_store),
) -> dict[str, Any]:
    agent = agents.get(agent_id)
    if agent is None:
        raise ApiError(404, "not_found", f"agent not found: {agent_id}")
    return ok(agent)


@router.patch("/v1/agents/{agent_id}")
def patch_agent(
    agent_id: str,
    body: AgentPatch,
    api_key: str = Depends(require_api_key),
    agents: AgentStore = Depends(get_agent_store),
    audit: AuditLog = Depends(get_audit_log),
) -> dict[str, Any]:
    changes = body.model_dump(exclude_none=True)
    try:
        agent = agents.update(
            agent_id,
            body.name.strip() if body.name is not None else None,
            body.status,
            body.metadata,
        )
    except sqlite3.IntegrityError:
        raise ApiError(409, "conflict", f"agent name already exists in env: {body.name!r}")
    if agent is None:
        raise ApiError(404, "not_found", f"agent not found: {agent_id}")
    if changes:
        audit.record(
            actor_for(api_key), "agent.update", agent_id, {"fields": sorted(changes)}
        )
    return ok(agent)
