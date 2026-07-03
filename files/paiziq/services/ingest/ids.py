"""Opaque prefixed identifiers and epoch-ms timestamps.

ID format per docs/06_API_CONTRACT.md §1.5: `<prefix>_<20 hex chars>`,
e.g. `org_1f2e3d...`. Prefixes: org, env, agt, key, pay, dec, rev,
pol, aud.
"""

from __future__ import annotations

import secrets
import time


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(10)}"


def now_ms() -> int:
    return int(time.time() * 1000)
