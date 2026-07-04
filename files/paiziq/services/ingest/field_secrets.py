"""Field-level secret encryption at rest (PZ-082).

When PAIZIQ_SECRETS_KEY is set, webhook signing secrets are Fernet-
encrypted before SQLite storage. API key hashes remain one-way SHA-256.
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Optional

_fernet = None


def _get_fernet(key: Optional[str]):
    global _fernet
    if not key:
        return None
    if _fernet is not None:
        return _fernet
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise RuntimeError(
            "PAIZIQ_SECRETS_KEY is set but cryptography is not installed"
        ) from exc
    digest = hashlib.sha256(key.encode()).digest()
    _fernet = Fernet(base64.urlsafe_b64encode(digest))
    return _fernet


def encrypt_secret(plaintext: str, master_key: Optional[str]) -> str:
    f = _get_fernet(master_key)
    if f is None:
        return plaintext
    return "enc:" + f.encrypt(plaintext.encode()).decode()


def decrypt_secret(stored: str, master_key: Optional[str]) -> str:
    if not stored.startswith("enc:"):
        return stored
    f = _get_fernet(master_key)
    if f is None:
        raise RuntimeError("encrypted secret stored but PAIZIQ_SECRETS_KEY is unset")
    return f.decrypt(stored[4:].encode()).decode()


def generate_webhook_secret() -> str:
    return "whsec_" + base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("=")
