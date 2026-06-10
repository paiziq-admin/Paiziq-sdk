"""Production BudgetStore implementations.

The in-memory store in `policy.py` is per-process and therefore dev-only.
Multi-process agent fleets share spend state through RedisBudgetStore,
which keeps each agent's spend history in a Redis sorted set:

    key:    paiziq:budget:{agent_id}
    member: "{uuid}:{amount}"   (unique per transaction, amount embedded)
    score:  unix timestamp of the spend

`spend_since` / `tx_count_since` are then range queries over the score,
and old entries are trimmed past the retention window so keys stay small.

The redis client is injected (any object satisfying the small `_RedisLike`
surface works), so unit tests run against a fake and the `redis` package
is only required when connecting by URL: `pip install paiziq[redis]`.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional, Protocol


class _RedisLike(Protocol):
    """Minimal slice of the redis-py client used by RedisBudgetStore."""

    def zadd(self, name: str, mapping: dict[str, float]) -> Any: ...
    def zrangebyscore(self, name: str, min: float, max: float) -> list[Any]: ...
    def zcount(self, name: str, min: float, max: float) -> int: ...
    def zremrangebyscore(self, name: str, min: float, max: float) -> Any: ...


class RedisBudgetStore:
    """Shared, atomic spend ledger for multi-process agent fleets.

    Implements the `BudgetStore` protocol from `paiziq.engine.policy`.
    """

    def __init__(
        self,
        client: Optional[_RedisLike] = None,
        url: Optional[str] = None,
        key_prefix: str = "paiziq:budget",
        retention_s: float = 35 * 86_400.0,  # > monthly window, so queries stay correct
    ) -> None:
        if client is None:
            if url is None:
                raise ValueError("RedisBudgetStore requires either a client or a url")
            try:
                import redis  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover - install-time guidance
                raise ImportError(
                    "RedisBudgetStore by URL requires the redis package. "
                    "Install it with: pip install 'paiziq[redis]'"
                ) from exc
            client = redis.Redis.from_url(url, decode_responses=True)
        self._client = client
        self._prefix = key_prefix
        self._retention_s = retention_s

    def _key(self, agent_id: str) -> str:
        return f"{self._prefix}:{agent_id}"

    def record_spend(self, agent_id: str, amount: float, ts: Optional[float] = None) -> None:
        ts = ts if ts is not None else time.time()
        member = f"{uuid.uuid4().hex}:{amount}"
        key = self._key(agent_id)
        self._client.zadd(key, {member: ts})
        # Opportunistic trim keeps the set bounded; correctness never depends on it.
        self._client.zremrangebyscore(key, float("-inf"), time.time() - self._retention_s)

    def spend_since(self, agent_id: str, since_ts: float) -> float:
        members = self._client.zrangebyscore(self._key(agent_id), since_ts, float("+inf"))
        total = 0.0
        for member in members:
            raw = member.decode() if isinstance(member, bytes) else str(member)
            try:
                total += float(raw.rsplit(":", 1)[1])
            except (IndexError, ValueError):
                continue  # foreign/corrupt member; never break the payment path
        return total

    def tx_count_since(self, agent_id: str, since_ts: float) -> int:
        return int(self._client.zcount(self._key(agent_id), since_ts, float("+inf")))
