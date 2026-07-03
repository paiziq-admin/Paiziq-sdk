"""Structured logging + debug mode tests (PZ-036)."""

import logging

import paiziq
from paiziq import DecisionStatus, PaiziqSDK, PaymentRequest, debug, get_logger, is_debug, log_event
from paiziq.logging import REDACTED, format_fields, redact


def req(**kw) -> PaymentRequest:
    defaults = dict(
        agent_id="agent-1",
        principal_id="user-1",
        merchant="acme.example",
        amount=25.0,
        currency="USD",
    )
    defaults.update(kw)
    return PaymentRequest(**defaults)


def teardown_function():
    debug(False)


# ── get_logger ───────────────────────────────────────────────────────────────

def test_get_logger_namespaces_under_paiziq():
    assert get_logger().name == "paiziq"
    assert get_logger("engine").name == "paiziq.engine"
    assert get_logger("paiziq.transport").name == "paiziq.transport"


# ── redaction ────────────────────────────────────────────────────────────────

def test_redact_masks_sensitive_fields_only():
    fields = {
        "api_key": "pz_live_123",
        "webhook_secret": "shhh",
        "Authorization": "Bearer abc",
        "session_token": "tok",
        "merchant": "acme.example",
        "amount": 25.0,
    }
    safe = redact(fields)
    assert safe["api_key"] == REDACTED
    assert safe["webhook_secret"] == REDACTED
    assert safe["Authorization"] == REDACTED
    assert safe["session_token"] == REDACTED
    assert safe["merchant"] == "acme.example"
    assert safe["amount"] == 25.0


def test_log_event_never_prints_secrets(caplog):
    logger = get_logger("test")
    with caplog.at_level(logging.INFO, logger="paiziq"):
        log_event(logger, "auth", api_key="pz_live_supersecret", user="u-1")
    record = caplog.records[-1]
    assert "pz_live_supersecret" not in record.getMessage()
    assert REDACTED in record.getMessage()
    assert record.paiziq_fields["api_key"] == REDACTED


# ── structured records ───────────────────────────────────────────────────────

def test_log_event_emits_key_value_message_and_extras(caplog):
    logger = get_logger("test")
    with caplog.at_level(logging.INFO, logger="paiziq"):
        log_event(logger, "decision", request_id="r-1", amount=25.0, status="approved")
    record = caplog.records[-1]
    message = record.getMessage()
    assert message.startswith("event=decision")
    assert "amount=25.0" in message
    assert "request_id=r-1" in message
    assert "status=approved" in message
    assert record.paiziq_event == "decision"
    assert record.paiziq_fields == {"request_id": "r-1", "amount": 25.0, "status": "approved"}


def test_format_fields_quotes_values_with_spaces():
    assert format_fields({"merchant": "big store"}) == 'merchant="big store"'
    assert format_fields({"b": 2, "a": 1}) == "a=1 b=2"


# ── debug toggle ─────────────────────────────────────────────────────────────

def test_debug_toggle_switches_level_and_is_idempotent():
    root = logging.getLogger("paiziq")
    assert not is_debug()
    debug()
    assert is_debug()
    assert root.level == logging.DEBUG
    debug()  # second call must not stack handlers
    assert sum(isinstance(h, logging.StreamHandler) for h in root.handlers) == 1
    debug(False)
    assert not is_debug()
    assert root.level == logging.WARNING


def test_debug_mode_surfaces_verbose_decision_logs(caplog):
    debug()
    sdk = PaiziqSDK()
    with caplog.at_level(logging.DEBUG, logger="paiziq"):
        decision = sdk.review_payment(req())
    assert decision.status is DecisionStatus.APPROVED
    decision_logs = [
        r for r in caplog.records if getattr(r, "paiziq_event", None) == "decision"
    ]
    assert len(decision_logs) == 1
    assert decision_logs[0].paiziq_fields["status"] == "approved"


def test_helpers_are_reexported_from_paiziq():
    for name in ("debug", "get_logger", "is_debug", "log_event"):
        assert hasattr(paiziq, name)
