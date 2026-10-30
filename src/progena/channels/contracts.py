"""Validation and redaction for ``channel:<id>`` documents.

A channel document holds a messaging channel's type, enabled flag, non-secret
settings and credential *references* (an environment variable name or
backend-encrypted ciphertext). The backend owns the adapters that run channels
and validates type-specific settings; this module is the schema floor both the
backend and the runtime's read-only ``/config/channels/`` view share.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from src.progena.documents.catalog import check_entry_schema
from src.progena.documents.kinds import CHANNEL_KEY_PREFIX

CHANNEL_SCHEMA = "channel"
_CHANNEL_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class CredentialSource(StrEnum):
    """Where a channel credential's secret value comes from (stored wire value)."""

    ENVIRONMENT = "environment"
    DATABASE = "database"


def validate_channel_id(channel_id: object) -> str:
    """Return ``channel_id`` when it is a valid id, else raise ``ValueError``."""
    if not isinstance(channel_id, str) or not _CHANNEL_ID_RE.match(channel_id):
        raise ValueError(
            "channel id must start with a lowercase letter and contain only "
            "lowercase letters, digits, '-' or '_' (max 64 characters)"
        )
    return channel_id


def channel_key(channel_id: str) -> str:
    """The state document key for one channel."""
    return f"{CHANNEL_KEY_PREFIX}{validate_channel_id(channel_id)}"


def channel_id_from_key(key: str) -> str | None:
    """The channel id named by a ``channel:<id>`` key, or ``None``."""
    if not key.startswith(CHANNEL_KEY_PREFIX):
        return None
    channel_id = key.removeprefix(CHANNEL_KEY_PREFIX)
    return channel_id if _CHANNEL_ID_RE.match(channel_id) else None


def validate_channel_document(channel_id: str, document: Any) -> dict[str, Any]:
    """Validate one stored channel document against ``channel.schema.v1.json``.

    The id inside the document must match its key. Type-specific settings are
    validated separately by the backend's channel type.
    """
    validate_channel_id(channel_id)
    if not isinstance(document, Mapping):
        raise ValueError(f"channel '{channel_id}' must be a JSON object")
    validated = dict(document)
    check_entry_schema(f"channel '{channel_id}'", validated, CHANNEL_SCHEMA)
    if validated["id"] != channel_id:
        raise ValueError(f"channel '{channel_id}' document has mismatched id {validated['id']!r}")
    return validated


def public_credential(reference: Any) -> dict[str, Any]:
    """A credential reference without its secret material.

    ``configured`` reports whether a value is available: the environment
    variable is set, or ciphertext is stored.
    """
    if not isinstance(reference, Mapping):
        return {"source": None, "env_var": None, "configured": False}
    source = reference.get("source")
    if source == CredentialSource.ENVIRONMENT:
        env_var = str(reference.get("env_var") or "")
        return {
            "source": CredentialSource.ENVIRONMENT.value,
            "env_var": env_var or None,
            "configured": bool(env_var and (os.getenv(env_var) or "").strip()),
        }
    if source == CredentialSource.DATABASE:
        return {
            "source": CredentialSource.DATABASE.value,
            "env_var": None,
            "configured": bool(reference.get("ciphertext")),
        }
    return {"source": None, "env_var": None, "configured": False}


def redacted_channel_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """The document with every credential replaced by its public view."""
    redacted = dict(document)
    credentials = document.get("credentials")
    redacted["credentials"] = {
        str(name): public_credential(reference)
        for name, reference in (credentials.items() if isinstance(credentials, Mapping) else ())
    }
    return redacted
