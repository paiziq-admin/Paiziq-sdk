"""Webhook signature verification tests (PZ-038)."""

import time

import pytest

import paiziq
from paiziq import sign_webhook_payload, verify_webhook_signature

SECRET = "whsec_" + "x" * 32  # constructed at runtime; not a live secret
PAYLOAD = '{"event": "payment.approved", "payment_id": "pay-1"}'


def now() -> int:
    return int(time.time())


# ── happy path ───────────────────────────────────────────────────────────────

def test_valid_signature_verifies():
    signature = sign_webhook_payload(PAYLOAD, SECRET)
    assert verify_webhook_signature(PAYLOAD, signature, SECRET) is True


def test_bytes_and_str_payloads_are_equivalent():
    signature = sign_webhook_payload(PAYLOAD.encode(), SECRET)
    assert verify_webhook_signature(PAYLOAD, signature, SECRET) is True
    assert verify_webhook_signature(PAYLOAD.encode(), signature, SECRET) is True


def test_signature_header_format():
    signature = sign_webhook_payload(PAYLOAD, SECRET, timestamp=1_700_000_000)
    assert signature.startswith("t=1700000000,v1=")
    assert len(signature.split("v1=", 1)[1]) == 64  # hex sha256


# ── invalid signatures ───────────────────────────────────────────────────────

def test_wrong_secret_fails():
    signature = sign_webhook_payload(PAYLOAD, SECRET)
    assert verify_webhook_signature(PAYLOAD, signature, "whsec_other") is False


def test_tampered_payload_fails():
    signature = sign_webhook_payload(PAYLOAD, SECRET)
    tampered = PAYLOAD.replace("pay-1", "pay-2")
    assert verify_webhook_signature(tampered, signature, SECRET) is False


def test_tampered_signature_hex_fails():
    signature = sign_webhook_payload(PAYLOAD, SECRET)
    head, hexpart = signature.split("v1=", 1)
    flipped = ("0" if hexpart[0] != "0" else "1") + hexpart[1:]
    assert verify_webhook_signature(PAYLOAD, head + "v1=" + flipped, SECRET) is False


# ── replay window ────────────────────────────────────────────────────────────

def test_expired_timestamp_fails():
    old = now() - 600
    signature = sign_webhook_payload(PAYLOAD, SECRET, timestamp=old)
    assert verify_webhook_signature(PAYLOAD, signature, SECRET, tolerance_s=300) is False


def test_future_timestamp_fails():
    future = now() + 600
    signature = sign_webhook_payload(PAYLOAD, SECRET, timestamp=future)
    assert verify_webhook_signature(PAYLOAD, signature, SECRET, tolerance_s=300) is False


def test_within_tolerance_passes_and_boundary_is_inclusive():
    ts = 1_700_000_000
    signature = sign_webhook_payload(PAYLOAD, SECRET, timestamp=ts)
    assert verify_webhook_signature(
        PAYLOAD, signature, SECRET, tolerance_s=300, now=ts + 300
    ) is True
    assert verify_webhook_signature(
        PAYLOAD, signature, SECRET, tolerance_s=300, now=ts + 301
    ) is False


# ── malformed input never raises ─────────────────────────────────────────────

@pytest.mark.parametrize(
    "bad_signature",
    [
        "",
        "garbage",
        "t=notanumber,v1=abc",
        "v1=deadbeef",           # missing timestamp
        "t=1700000000",          # missing v1
        "t=1700000000,v1=",      # empty v1
        "t=,v1=deadbeef",
        None,
        12345,
    ],
)
def test_malformed_signatures_return_false(bad_signature):
    assert verify_webhook_signature(PAYLOAD, bad_signature, SECRET) is False


def test_empty_secret_is_a_programmer_error():
    signature = sign_webhook_payload(PAYLOAD, SECRET)
    with pytest.raises(ValueError):
        verify_webhook_signature(PAYLOAD, signature, "")
    with pytest.raises(ValueError):
        sign_webhook_payload(PAYLOAD, "")


def test_helpers_are_reexported_from_paiziq():
    assert hasattr(paiziq, "verify_webhook_signature")
    assert hasattr(paiziq, "sign_webhook_payload")
