"""Structured logging + debug mode for the SDK (PZ-036).

Stdlib-only helpers producing structured ``key=value`` log records under
the ``paiziq`` logger hierarchy, plus a :func:`debug` toggle that turns
on verbose decision/transport logs. Sensitive fields (API keys, secrets,
tokens, ...) are always redacted — logging never prints secrets.
"""

from __future__ import annotations

import json
import logging as _stdlib_logging
import sys
from typing import Any, Optional

REDACTED = "[REDACTED]"

#: Any field whose lowercased name contains one of these substrings is
#: redacted before it reaches a log record.
_SENSITIVE_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
)

_ROOT_LOGGER_NAME = "paiziq"
_debug_handler: Optional[_stdlib_logging.Handler] = None


def get_logger(name: str = _ROOT_LOGGER_NAME) -> _stdlib_logging.Logger:
    """Return a logger in the ``paiziq`` hierarchy."""
    if name != _ROOT_LOGGER_NAME and not name.startswith(_ROOT_LOGGER_NAME + "."):
        name = f"{_ROOT_LOGGER_NAME}.{name}"
    return _stdlib_logging.getLogger(name)


def redact(fields: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *fields* with sensitive values replaced."""
    return {
        key: REDACTED
        if any(marker in key.lower() for marker in _SENSITIVE_MARKERS)
        else value
        for key, value in fields.items()
    }


def _format_value(value: Any) -> str:
    if isinstance(value, str):
        return value if value and " " not in value and "=" not in value else json.dumps(value)
    return json.dumps(value, default=str)


def format_fields(fields: dict[str, Any]) -> str:
    """Render fields as a stable ``key=value`` string (keys sorted)."""
    return " ".join(f"{k}={_format_value(v)}" for k, v in sorted(fields.items()))


def log_event(
    logger: _stdlib_logging.Logger,
    event: str,
    level: int = _stdlib_logging.INFO,
    **fields: Any,
) -> None:
    """Emit one structured record: ``event=<event> key=value ...``.

    Sensitive fields are redacted unconditionally; callers cannot opt
    out. The raw event name and redacted fields are also attached to the
    record (``record.paiziq_event`` / ``record.paiziq_fields``) for
    structured handlers.
    """
    safe = redact(fields)
    message = f"event={event}"
    if safe:
        message += f" {format_fields(safe)}"
    logger.log(level, message, extra={"paiziq_event": event, "paiziq_fields": safe})


def debug(enabled: bool = True) -> None:
    """Toggle verbose SDK logging (decision engine, transport, exporters).

    Enabling sets the ``paiziq`` logger to DEBUG and attaches a stderr
    handler; disabling restores WARNING and removes the handler. Safe to
    call repeatedly.
    """
    global _debug_handler
    root = _stdlib_logging.getLogger(_ROOT_LOGGER_NAME)
    if enabled:
        root.setLevel(_stdlib_logging.DEBUG)
        if _debug_handler is None:
            _debug_handler = _stdlib_logging.StreamHandler(sys.stderr)
            _debug_handler.setFormatter(
                _stdlib_logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
            )
            root.addHandler(_debug_handler)
    else:
        root.setLevel(_stdlib_logging.WARNING)
        if _debug_handler is not None:
            root.removeHandler(_debug_handler)
            _debug_handler = None


def is_debug() -> bool:
    """Whether :func:`debug` mode is currently enabled."""
    return _debug_handler is not None
