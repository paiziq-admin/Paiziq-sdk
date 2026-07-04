"""In-memory token-bucket rate limiting (PZ-083)."""

from __future__ import annotations

import threading
import time
from typing import Optional


class RateLimiter:
    """Per-key requests-per-minute limiter (stdlib-only)."""

    def __init__(self, rpm: int) -> None:
        self._rpm = max(rpm, 1)
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, key: str, now: Optional[float] = None) -> bool:
        ts = time.monotonic() if now is None else now
        refill = self._rpm / 60.0
        with self._lock:
            tokens, updated = self._buckets.get(key, (float(self._rpm), ts))
            tokens = min(self._rpm, tokens + (ts - updated) * refill)
            if tokens < 1.0:
                self._buckets[key] = (tokens, ts)
                return False
            self._buckets[key] = (tokens - 1.0, ts)
            return True
