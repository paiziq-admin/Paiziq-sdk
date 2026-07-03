"""Webhook signature verification (PZ-038).

Paiziq webhooks are signed with HMAC-SHA256 over
``"{timestamp}.{payload}"``. Signature header format:

    t=<unix timestamp>,v1=<hex hmac-sha256>

`verify_webhook_signature` checks the signature in constant time and
rejects stale timestamps (replay-window check). It never raises for
bad input from the wire — malformed headers simply verify as False.
Only a missing/empty *secret* is a programmer error and raises.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional, Union

DEFAULT_TOLERANCE_S = 300  # 5 minutes, mirrors common provider defaults


def sign_webhook_payload(
    payload: Union[str, bytes],
    secret: str,
    timestamp: Optional[int] = None,
) -> str:
    """Produce a ``t=...,v1=...`` signature header for *payload*.

    Used by the Paiziq backend when sending webhooks and by tests /
    local development to fabricate valid signatures.
    """
    if not secret:
        raise ValueError("secret must be a non-empty string")
    if timestamp is None:
        timestamp = int(time.time())
    body = payload.encode("utf-8") if isinstance(payload, str) else payload
    mac = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode("utf-8") + body,
        hashlib.sha256,
    )
    return f"t={timestamp},v1={mac.hexdigest()}"


def verify_webhook_signature(
    payload: Union[str, bytes],
    signature: str,
    secret: str,
    tolerance_s: int = DEFAULT_TOLERANCE_S,
    now: Optional[int] = None,
) -> bool:
    """Return True iff *signature* is valid for *payload* and fresh.

    - Constant-time comparison (``hmac.compare_digest``).
    - ``tolerance_s`` bounds the replay window on both sides
      (stale *and* future timestamps are rejected).
    - Malformed signatures/timestamps return False, never raise.
    """
    if not secret:
        raise ValueError("secret must be a non-empty string")
    if not isinstance(signature, str) or not signature:
        return False

    timestamp: Optional[int] = None
    provided: Optional[str] = None
    for part in signature.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                return False
        elif key == "v1":
            provided = value
    if timestamp is None or not provided:
        return False

    current = int(time.time()) if now is None else now
    if abs(current - timestamp) > tolerance_s:
        return False

    expected = sign_webhook_payload(payload, secret, timestamp=timestamp)
    expected_hex = expected.split("v1=", 1)[1]
    return hmac.compare_digest(expected_hex, provided)
