"""Validation and migration for the virtual ``/config/profiles.json`` document."""

from __future__ import annotations

import json
from typing import Any

MODEL_PROFILES_DOC_KEY = "profile"
_LEGACY_AZURE_PROVIDER = "azure_openai"
_OPTIONAL_REASONING_FIELDS = (
    "reasoning_enabled",
    "reasoning_effort",
    "reasoning_summary",
)

_SUPPORTED_PROFILE_FIELDS = frozenset({"provider_id", "deployment", *_OPTIONAL_REASONING_FIELDS})


def default_model_profiles_payload() -> dict[str, Any]:
    """Return no profiles until an administrator selects a persisted provider."""
    return {}


def _normalize_provider_id(value: Any, *, profile_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"profile {profile_name!r}: provider_id must be a non-empty string")
    return value.strip()


def _normalize_profile(name: str, profile: Any) -> dict[str, Any]:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("profile names must be non-empty strings")
    if not isinstance(profile, dict):
        raise ValueError(f"profile {name!r} must be an object")
    unsupported = set(profile) - _SUPPORTED_PROFILE_FIELDS
    if unsupported:
        raise ValueError(f"profile {name!r}: unsupported fields {sorted(unsupported)}")
    deployment = profile.get("deployment")
    if not isinstance(deployment, str) or not deployment.strip():
        raise ValueError(f"profile {name!r}: deployment must be a non-empty string")
    normalized: dict[str, Any] = {
        "provider_id": _normalize_provider_id(profile.get("provider_id"), profile_name=name),
        "deployment": deployment.strip(),
    }
    for field in _OPTIONAL_REASONING_FIELDS:
        if field not in profile:
            continue
        value = profile[field]
        if field == "reasoning_enabled":
            if not isinstance(value, bool):
                raise ValueError(f"profile {name!r}: reasoning_enabled must be boolean")
        elif not isinstance(value, str) or not value.strip():
            raise ValueError(f"profile {name!r}: {field} must be a non-empty string")
        normalized[field] = value if isinstance(value, bool) else value.strip()
    return normalized


def migrate_legacy_model_profiles_payload(payload: Any, foundry_provider_id: str) -> dict[str, Any]:
    """Convert legacy Azure discriminator profiles into provider references.

    Callers must persist the returned payload. This intentionally has no
    fallback for the historical discriminator: after that one write, every
    profile is validated exclusively through ``provider_id``.
    """
    provider_id = _normalize_provider_id(foundry_provider_id, profile_name="legacy migration")
    if not isinstance(payload, dict):
        raise ValueError("profile document must be a JSON object")
    converted: dict[str, Any] = {}
    for name, profile in payload.items():
        if not isinstance(profile, dict):
            raise ValueError(f"profile {name!r} must be an object")
        if profile.get("provider") == _LEGACY_AZURE_PROVIDER and "provider_id" not in profile:
            # Legacy documents carried connection settings, including secrets.
            # Connections now resolve only from the referenced provider, so
            # retain solely the canonical model behaviour fields.
            converted[name] = {
                key: value
                for key, value in profile.items()
                if key in _SUPPORTED_PROFILE_FIELDS and key != "provider_id"
            } | {"provider_id": provider_id}
        else:
            converted[name] = dict(profile)
    return validate_model_profiles_payload(converted)


def validate_model_profiles_payload(payload: Any) -> dict[str, Any]:
    """Validate canonical profile provider references and model behaviour."""
    if not isinstance(payload, dict):
        raise ValueError("profile document must be a JSON object")
    normalized: dict[str, Any] = {}
    for name, profile in payload.items():
        stripped = name.strip() if isinstance(name, str) else ""
        if stripped in normalized:
            raise ValueError(f"ambiguous profile names: {stripped!r} appears more than once")
        normalized[stripped] = _normalize_profile(name, profile)
    return normalized


def validate_model_profiles_document(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"profiles.json must be valid JSON: {exc}") from exc
    return validate_model_profiles_payload(payload)


async def resolve_model_profile(state_manager: Any, name: str) -> Any:
    """Return a profile-bound model whose connection resolves from Postgres."""
    from ..agent.adapters import ProviderChatModel
    from ..config import LLMConfig

    profiles = validate_model_profiles_payload(await state_manager.get_model_profiles_config())
    if name not in profiles:
        raise ValueError(f"Unknown model profile {name!r}")
    profile = profiles[name]
    template = LLMConfig(
        reasoning_enabled=profile.get("reasoning_enabled", True),
        reasoning_effort=profile.get("reasoning_effort", LLMConfig.reasoning_effort),
        reasoning_summary=profile.get("reasoning_summary", LLMConfig.reasoning_summary),
    )
    return ProviderChatModel(
        template=template,
        provider_id=profile["provider_id"],
        model_override=profile["deployment"],
    )
