"""Background webhook delivery worker (PZ-076/PZ-077)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import metrics_rollup
import retention
from event_router import EventRouter
from stores.webhooks import WebhookStore
from webhook_sign import sign_webhook_payload

log = logging.getLogger(__name__)
_USER_AGENT = "Paiziq-Webhook/1.0"
_TIMEOUT_S = 10


def _deliver(url: str, body: bytes, signature: str) -> tuple[int, Optional[str]]:
    req = Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
            "Paiziq-Signature": signature,
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=_TIMEOUT_S) as resp:
            code = resp.getcode()
            if 200 <= code < 300:
                return code, None
            return code, f"unexpected status {code}"
    except HTTPError as exc:
        return exc.code, str(exc.reason)
    except URLError as exc:
        return 0, str(exc.reason)


async def process_due_deliveries(webhooks: WebhookStore) -> int:
    processed = 0
    for delivery in webhooks.claim_due():
        endpoint = webhooks.get_endpoint(delivery["endpoint_id"])
        if endpoint is None or endpoint["status"] != "active":
            webhooks.mark_failed(
                delivery["id"], delivery["attempts"], "endpoint inactive", None
            )
            continue
        secret = webhooks.get_endpoint_secret(delivery["endpoint_id"])
        if not secret:
            webhooks.mark_failed(
                delivery["id"], delivery["attempts"], "missing secret", None
            )
            continue
        body = json.dumps(delivery["payload"], separators=(",", ":")).encode()
        signature = sign_webhook_payload(body, secret)
        code, error = await asyncio.to_thread(_deliver, endpoint["url"], body, signature)
        attempt = delivery["attempts"] + 1
        webhooks.append_log(delivery["id"], attempt, code or None, error)
        if error is None:
            webhooks.mark_delivered(delivery["id"])
        else:
            webhooks.mark_failed(delivery["id"], delivery["attempts"], error, code)
        processed += 1
    return processed


async def worker_loop(
    webhooks: WebhookStore,
    router: EventRouter,
    conn: Any,
    lock: Any,
    retention_job: Optional[retention.RetentionJob],
    interval_s: float,
    stop: asyncio.Event,
) -> None:
    tick = 0
    while not stop.is_set():
        try:
            await process_due_deliveries(webhooks)
            router.check_sla_breaches()
            if tick % 60 == 0:
                metrics_rollup.run_rollups(conn, lock)
            if retention_job and tick % 3600 == 0:
                retention_job.run()
        except Exception:
            log.exception("webhook worker tick failed")
        tick += 1
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass
