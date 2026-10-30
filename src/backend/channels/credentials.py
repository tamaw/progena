"""Channel secrets: environment references or backend-encrypted ciphertext.

Database-stored secrets reuse ``PROGENA_PROVIDER_ENCRYPTION_KEY`` (the Fernet
key that already protects provider API keys), so an installation has one key
to manage. Plain-text secrets never enter a channel document.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from src.progena.channels.contracts import CredentialSource
from src.progena.settings.providers import PROVIDER_ENCRYPTION_KEY_ENV as ENCRYPTION_KEY_ENV


def _fernet() -> Fernet:
    key = (os.getenv(ENCRYPTION_KEY_ENV) or "").strip()
    if not key:
        raise ValueError(f"{ENCRYPTION_KEY_ENV} is required to store channel secrets")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{ENCRYPTION_KEY_ENV} is invalid") from exc


def database_storage_available() -> bool:
    """Whether secrets can be stored encrypted in the channel document."""
    try:
        _fernet()
    except ValueError:
        return False
    return True


def encrypt_secret(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("secret value must be a non-empty string")
    return _fernet().encrypt(value.strip().encode("utf-8")).decode("ascii")


def resolve_secret(reference: Any) -> str | None:
    """The secret a reference points at, or ``None`` when it is unavailable.

    Raises ``ValueError`` when stored ciphertext cannot be decrypted, so the
    channel reports a clear error instead of looking unconfigured.
    """
    if not isinstance(reference, Mapping):
        return None
    if reference.get("source") == CredentialSource.ENVIRONMENT:
        value = (os.getenv(str(reference.get("env_var") or "")) or "").strip()
        return value or None
    if reference.get("source") == CredentialSource.DATABASE:
        ciphertext = reference.get("ciphertext")
        if not isinstance(ciphertext, str) or not ciphertext:
            return None
        try:
            return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise ValueError("stored secret cannot be decrypted with the current key") from exc
    return None
