"""HMAC-SHA256 webhook signing for outbound delivery (PZ-077).

Matches the SDK format in paiziq.webhooks so subscribers can verify
with verify_webhook_signature. Stdlib-only — ingest does not depend on
the SDK at runtime.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional, Union


def sign_webhook_payload(
    payload: Union[str, bytes],
    secret: str,
    timestamp: Optional[int] = None,
) -> str:
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
