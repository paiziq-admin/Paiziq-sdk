"""User notifications.

Fired when (a) harmful intent is suspected, (b) a payment is rejected,
or (c) human review is required. Notifiers are pluggable; production
deployments typically use WebhookNotifier pointed at the Paiziq
notification service (which fans out to Slack/email/dashboard).
Notification failures are logged, never raised.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional, Protocol

from ..models import Decision, PaymentRequest, RiskFlag, _now_ms

logger = logging.getLogger("paiziq.notifications")

SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"


@dataclass
class Notification:
    severity: str
    title: str
    message: str
    request_id: str
    risk_flags: list[str] = field(default_factory=list)
    created_at_ms: int = field(default_factory=_now_ms)

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "request_id": self.request_id,
            "risk_flags": self.risk_flags,
            "created_at_ms": self.created_at_ms,
        }


class Notifier(Protocol):
    def send(self, notification: Notification) -> None: ...


class ConsoleNotifier:
    def __init__(self) -> None:
        self.sent: list[Notification] = []

    def send(self, notification: Notification) -> None:
        self.sent.append(notification)
        logger.warning("PAIZIQ ALERT [%s] %s — %s", notification.severity, notification.title, notification.message)


class WebhookNotifier:
    """POSTs notifications to a webhook (Paiziq notification service, Slack, etc.)."""

    def __init__(self, url: str, api_key: Optional[str] = None, timeout_s: float = 5.0) -> None:
        self.url = url
        self.api_key = api_key
        self.timeout_s = timeout_s

    def send(self, notification: Notification) -> None:
        headers = {"Content-Type": "application/json", "User-Agent": "paiziq-sdk/0.1.0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            self.url, data=json.dumps(notification.to_dict()).encode(), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s):
                pass
        except (urllib.error.URLError, OSError) as exc:
            logger.error("paiziq notification delivery failed: %s", exc)


class NotificationRouter:
    """Maps decisions to notifications and fans out to registered notifiers."""

    def __init__(self, notifiers: Optional[list[Notifier]] = None) -> None:
        self.notifiers: list[Notifier] = notifiers or [ConsoleNotifier()]

    def notify_decision(self, request: PaymentRequest, decision: Decision) -> None:
        notification = self._build(request, decision)
        if notification is None:
            return
        for n in self.notifiers:
            try:
                n.send(notification)
            except Exception:
                logger.exception("notifier %s failed", type(n).__name__)

    def _build(self, request: PaymentRequest, decision: Decision) -> Optional[Notification]:
        flags = [f.value for f in decision.risk_flags]
        base = (
            f"Agent '{request.agent_id}' attempted to pay {request.amount:.2f} "
            f"{request.currency} to '{request.merchant}'."
        )
        if RiskFlag.HARMFUL_INTENT_SUSPECTED in decision.risk_flags:
            return Notification(
                SEVERITY_CRITICAL,
                "Harmful intent suspected",
                base + " The agent's stated intent matched harmful/evasive patterns. "
                + " ".join(decision.reasons),
                request.request_id,
                flags,
            )
        if decision.status.value == "rejected":
            return Notification(
                SEVERITY_WARNING,
                "Payment rejected",
                base + " Rejected: " + "; ".join(decision.reasons),
                request.request_id,
                flags,
            )
        if decision.status.value == "needs_review":
            return Notification(
                SEVERITY_INFO,
                "Payment awaiting your review",
                base + " Reasons: " + "; ".join(decision.reasons),
                request.request_id,
                flags,
            )
        return None
