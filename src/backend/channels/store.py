"""Persistence for ``channel:<id>`` documents (blocking; call off the event loop)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.backend.channels.credentials import encrypt_secret
from src.backend.channels.registry import CHANNEL_TYPES
from src.backend.storage.documents import PROGENA_STATE_PREFIX, BackendDocumentStore
from src.progena.channels.contracts import (
    CredentialSource,
    channel_id_from_key,
    channel_key,
    validate_channel_document,
)
from src.progena.documents.kinds import CHANNEL_KEY_PREFIX
from src.progena.utils import utcnow_isoformat

logger = logging.getLogger(__name__)

_PATCH_FIELDS = frozenset({"enabled", "settings", "credentials"})


@dataclass(frozen=True)
class StoredChannel:
    """One stored channel document; ``error`` explains an invalid document."""

    id: str
    raw: Any
    document: dict[str, Any] | None
    error: str | None


def load_channels(store: BackendDocumentStore) -> dict[str, StoredChannel]:
    """Every channel document by id, validated against the channel schema."""
    channels: dict[str, StoredChannel] = {}
    for key, stored in store.list_documents(PROGENA_STATE_PREFIX, CHANNEL_KEY_PREFIX):
        channel_id = channel_id_from_key(key)
        if channel_id is None:
            logger.warning("Ignoring channel document with invalid key %r", key)
            continue
        try:
            document = validate_channel_document(channel_id, stored.payload)
        except ValueError as exc:
            channels[channel_id] = StoredChannel(channel_id, stored.payload, None, str(exc))
            continue
        channels[channel_id] = StoredChannel(channel_id, stored.payload, document, None)
    return channels


def seed_channels(store: BackendDocumentStore) -> list[str]:
    """Create the document of every registered channel type that has none yet.

    The first document takes its values from the type's legacy environment
    variables, so an existing installation keeps working; afterwards the
    document is the only source of truth. Returns the ids it created.
    """
    created: list[str] = []
    for type_id, adapter in CHANNEL_TYPES.items():
        key = channel_key(type_id)
        if store.get_document(PROGENA_STATE_PREFIX, key) is not None:
            continue
        enabled, settings, credentials = adapter.seed_from_environment()
        now = utcnow_isoformat()
        document = validate_channel_document(
            type_id,
            {
                "id": type_id,
                "type": type_id,
                "name": adapter.spec.name,
                "enabled": enabled,
                "settings": settings,
                "credentials": credentials,
                "created_at": now,
                "updated_at": now,
            },
        )

        def create(current: Any, document: dict[str, Any] = document) -> Any:
            # Another backend may have created it since the read above.
            return current if current is not None else document

        store.mutate_document(PROGENA_STATE_PREFIX, key, create)
        created.append(type_id)
    return created


def _credential_reference(adapter_name: str, field: Any, value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"credentials.{field.key} must be an object or null")
    source = value.get("source")
    if source == CredentialSource.ENVIRONMENT:
        if set(value) - {"source", "env_var"}:
            raise ValueError(f"credentials.{field.key} accepts only source and env_var")
        return {
            "source": CredentialSource.ENVIRONMENT.value,
            "env_var": field.validate_env_var(value.get("env_var")),
        }
    if source == CredentialSource.DATABASE:
        if set(value) - {"source", "value"}:
            raise ValueError(f"credentials.{field.key} accepts only source and value")
        try:
            return {
                "source": CredentialSource.DATABASE.value,
                "ciphertext": encrypt_secret(value.get("value")),
            }
        except ValueError as exc:
            raise ValueError(f"{adapter_name} {field.label}: {exc}") from exc
    allowed = " or ".join(f"'{item.value}'" for item in CredentialSource)
    raise ValueError(f"credentials.{field.key}.source must be {allowed}")


def update_channel(store: BackendDocumentStore, channel_id: str, patch: Any) -> dict[str, Any]:
    """Apply a browser PATCH to one channel atomically and return the document.

    Raises ``LookupError`` for an unknown channel and ``ValueError`` for an
    invalid patch.
    """
    if not isinstance(patch, Mapping) or not patch:
        raise ValueError("Body must be an object with enabled, settings or credentials")
    unknown = sorted(str(key) for key in patch if key not in _PATCH_FIELDS)
    if unknown:
        raise ValueError(f"Unsupported fields: {', '.join(unknown)}")
    if "enabled" in patch and not isinstance(patch["enabled"], bool):
        raise ValueError("enabled must be a boolean")
    if "credentials" in patch and not isinstance(patch["credentials"], Mapping):
        raise ValueError("credentials must be an object")
    key = channel_key(channel_id)

    def mutator(current: Any) -> Any:
        if current is None:
            raise LookupError(f"Channel {channel_id!r} not found")
        document = validate_channel_document(channel_id, current)
        adapter = CHANNEL_TYPES.get(document["type"])
        if adapter is None:
            raise ValueError(f"Channel type {document['type']!r} is not supported")
        spec = adapter.spec
        if "enabled" in patch:
            document["enabled"] = patch["enabled"]
        if "settings" in patch:
            changes = spec.validate_settings(patch["settings"], partial=True)
            document["settings"] = spec.validate_settings({**document["settings"], **changes})
        if "credentials" in patch:
            credentials = dict(document["credentials"])
            for name, value in patch["credentials"].items():
                field = spec.credential(str(name))
                reference = _credential_reference(spec.name, field, value)
                if reference is None:
                    credentials.pop(field.key, None)
                else:
                    credentials[field.key] = reference
            document["credentials"] = credentials
        document["updated_at"] = utcnow_isoformat()
        return validate_channel_document(channel_id, document)

    return store.mutate_document(PROGENA_STATE_PREFIX, key, mutator)
