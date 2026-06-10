"""PII scrubbing hook applied to spans before export.

Traces leave the customer's environment, so anything that may contain
personal data — `intent_description` text, free-form metadata — can be
redacted before the span reaches an exporter. Wrap any exporter:

    exporter = ScrubbingExporter(HTTPExporter(endpoint, key), PIIScrubber())
    sdk = PaiziqSDK(policy=..., exporters=[exporter])

`PIIScrubber` is a plain callable, so teams can also supply their own
callable for custom redaction policies.

Two redaction mechanisms, both configurable:

* pattern scrubbing — regexes applied to every string value (emails,
  payment-card-like digit runs, SSN-shaped numbers by default);
* key redaction — attribute/event-payload keys whose values are replaced
  wholesale (for fields known to be sensitive regardless of content).

Scrubbing failures follow the SDK invariant: logged, never raised.

`ScrubbingExporter` wraps any exporter so scrubbing can also be applied
without touching the Tracer construction.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Iterable, Optional, Pattern

from .tracer import Exporter, Span

logger = logging.getLogger("paiziq.tracing.scrub")

DEFAULT_PATTERNS: dict[str, str] = {
    "email": r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b",
    "card": r"\b(?:\d[ -]?){13,19}\b",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
}


class PIIScrubber:
    """Redacts PII from span attributes and event payloads in place."""

    def __init__(
        self,
        patterns: Optional[dict[str, str]] = None,
        extra_patterns: Optional[dict[str, str]] = None,
        redact_keys: Optional[Iterable[str]] = None,
    ) -> None:
        merged = dict(DEFAULT_PATTERNS if patterns is None else patterns)
        merged.update(extra_patterns or {})
        self._patterns: dict[str, Pattern[str]] = {
            label: re.compile(expr) for label, expr in merged.items()
        }
        self._redact_keys = {k.lower() for k in (redact_keys or [])}

    # ── hook entrypoint ──────────────────────────────────────────────────

    def __call__(self, span: Span) -> None:
        try:
            span.attributes = self._scrub_value(span.attributes, key=None)
            span.events = self._scrub_value(span.events, key=None)
        except Exception:  # observability must never break the agent
            logger.exception("paiziq PII scrubber failed on span %s", span.name)

    # ── recursive scrubbing ──────────────────────────────────────────────

    def _scrub_value(self, value: Any, key: Optional[str]) -> Any:
        if key is not None and key.lower() in self._redact_keys:
            return "[REDACTED]"
        if isinstance(value, str):
            return self._scrub_text(value)
        if isinstance(value, dict):
            return {k: self._scrub_value(v, key=str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [self._scrub_value(v, key=None) for v in value]
        return value

    def _scrub_text(self, text: str) -> str:
        for label, pattern in self._patterns.items():
            text = pattern.sub(f"[REDACTED:{label}]", text)
        return text


class ScrubbingExporter:
    """Exporter decorator: scrubs every span, then delegates.

    Implements the `Exporter` protocol, so it slots anywhere an exporter
    is accepted: `PaiziqSDK(exporters=[ScrubbingExporter(inner)])`.
    """

    def __init__(
        self,
        inner: Exporter,
        scrubber: Optional[Callable[[Span], None]] = None,
    ) -> None:
        self.inner = inner
        self.scrubber = scrubber or PIIScrubber()

    def export(self, spans: list[Span]) -> None:
        for span in spans:
            try:
                self.scrubber(span)
            except Exception:  # scrubbing must never break the agent
                logger.exception("paiziq span scrubber failed on %s", span.name)
        self.inner.export(spans)

    def shutdown(self) -> None:
        self.inner.shutdown()
