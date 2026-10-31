"""Validation, migration and optional pricing for ``/config/profiles.json``.

Model profiles refer exclusively to persisted providers. Legacy Azure discriminator
profiles are migrated once after a default Foundry provider exists. Every profile
field is top-level (no nested objects) so each maps to one form field. Optional
pricing rates are explicit estimates; they never infer a deployment's model or
service tier, and are all present or all absent.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

MODEL_PROFILES_DOC_KEY = "profile"
_LEGACY_AZURE_PROVIDER = "azure_openai"
_OPTIONAL_REASONING_FIELDS = (
    "reasoning_enabled",
    "reasoning_effort",
    "reasoning_summary",
)
_PRICING_RATE_FIELDS = (
    "input_per_million",
    "cached_input_per_million",
    "output_per_million",
)
PRICING_FIELDS = ("currency", *_PRICING_RATE_FIELDS)
DESCRIPTION_MAX_LENGTH = 280
_SUPPORTED_PROFILE_FIELDS = frozenset(
    {
        "description",
        "provider_id",
        "deployment",
        "context_window",
        "cache_ttl_seconds",
        *_OPTIONAL_REASONING_FIELDS,
        *PRICING_FIELDS,
    }
)
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_DECIMAL_STRING_PATTERN = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d+)?$")
_MAX_PRICING_DECIMAL_PLACES = 12


def default_model_profiles_payload() -> dict[str, Any]:
    """Return no profiles until an administrator selects a persisted provider."""
    return {}


def _normalize_provider_id(value: Any, *, profile_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"profile {profile_name!r}: provider_id must be a non-empty string")
    return value.strip()


def _normalize_pricing(name: str, profile: dict[str, Any]) -> dict[str, str]:
    """Validate the optional current-rate estimate stored on one profile.

    The four pricing fields are top-level and all-or-nothing: none means the
    profile is unpriced.
    """
    present = [field for field in PRICING_FIELDS if field in profile]
    if not present:
        return {}
    missing = [field for field in PRICING_FIELDS if field not in profile]
    if missing:
        raise ValueError(
            f"profile {name!r}: pricing needs all of {list(PRICING_FIELDS)}; missing {missing}"
        )

    currency = profile["currency"]
    if not isinstance(currency, str) or not _CURRENCY_PATTERN.fullmatch(currency):
        raise ValueError(f"profile {name!r}: currency must be an uppercase ISO 4217 code")

    normalized = {"currency": currency}
    for field in _PRICING_RATE_FIELDS:
        value = profile[field]
        if (
            not isinstance(value, str)
            or not value.strip()
            or not _DECIMAL_STRING_PATTERN.fullmatch(value.strip())
        ):
            raise ValueError(f"profile {name!r}: {field} must be a finite non-negative decimal")
        try:
            decimal = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(
                f"profile {name!r}: {field} must be a finite non-negative decimal"
            ) from exc
        if not decimal.is_finite() or decimal < 0:
            raise ValueError(f"profile {name!r}: {field} must be a finite non-negative decimal")
        exponent = decimal.as_tuple().exponent
        if isinstance(exponent, int) and -exponent > _MAX_PRICING_DECIMAL_PLACES:
            raise ValueError(
                f"profile {name!r}: {field} has more than "
                f"{_MAX_PRICING_DECIMAL_PLACES} decimal places"
            )
        normalized[field] = value.strip()
    return normalized


def profile_pricing(profile: Any) -> dict[str, str] | None:
    """The pricing fields of a validated profile, or ``None`` when unpriced."""
    if not isinstance(profile, dict) or not all(field in profile for field in PRICING_FIELDS):
        return None
    return {field: profile[field] for field in PRICING_FIELDS}


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
    if "description" in profile:
        description = profile["description"]
        if not isinstance(description, str):
            raise ValueError(f"profile {name!r}: description must be a string")
        description = description.strip()
        if len(description) > DESCRIPTION_MAX_LENGTH:
            raise ValueError(
                f"profile {name!r}: description must be at most {DESCRIPTION_MAX_LENGTH} characters"
            )
        if description:
            normalized["description"] = description
    normalized.update(_normalize_pricing(name, profile))
    if "context_window" in profile:
        context_window = profile["context_window"]
        if type(context_window) is not int or context_window <= 0:
            raise ValueError(f"profile {name!r}: context_window must be a positive integer")
        normalized["context_window"] = context_window
    if "cache_ttl_seconds" in profile:
        cache_ttl_seconds = profile["cache_ttl_seconds"]
        if type(cache_ttl_seconds) is not int or cache_ttl_seconds <= 0:
            raise ValueError(f"profile {name!r}: cache_ttl_seconds must be a positive integer")
        normalized["cache_ttl_seconds"] = cache_ttl_seconds
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
