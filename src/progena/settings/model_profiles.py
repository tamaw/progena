"""Validation, migration and optional pricing for ``/config/profiles.json``.

Model profiles refer exclusively to persisted providers. Legacy Azure discriminator
profiles are migrated once after a default Foundry provider exists. Optional pricing
rates are explicit estimates; they never infer a deployment's model or service tier.
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
_PRICING_FIELDS = ("currency", *_PRICING_RATE_FIELDS)
_SUPPORTED_PROFILE_FIELDS = frozenset(
    {"provider_id", "deployment", "pricing", "context_window", *_OPTIONAL_REASONING_FIELDS}
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


def _normalize_pricing(name: str, pricing: Any) -> dict[str, str] | None:
    """Validate the optional current-rate estimate stored with one profile."""
    if pricing is None:
        return None
    if not isinstance(pricing, dict):
        raise ValueError(f"profile {name!r}: pricing must be an object or null")
    extra_fields = set(pricing) - set(_PRICING_FIELDS)
    missing_fields = set(_PRICING_FIELDS) - set(pricing)
    if extra_fields or missing_fields:
        details = []
        if extra_fields:
            details.append(f"unsupported fields {sorted(extra_fields)}")
        if missing_fields:
            details.append(f"missing fields {sorted(missing_fields)}")
        raise ValueError(f"profile {name!r}: pricing has {', '.join(details)}")

    currency = pricing["currency"]
    if not isinstance(currency, str) or not _CURRENCY_PATTERN.fullmatch(currency):
        raise ValueError(f"profile {name!r}: pricing.currency must be an uppercase ISO 4217 code")

    normalized = {"currency": currency}
    for field in _PRICING_RATE_FIELDS:
        value = pricing[field]
        if (
            not isinstance(value, str)
            or not value.strip()
            or not _DECIMAL_STRING_PATTERN.fullmatch(value.strip())
        ):
            raise ValueError(
                f"profile {name!r}: pricing.{field} must be a finite non-negative decimal"
            )
        try:
            decimal = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(
                f"profile {name!r}: pricing.{field} must be a finite non-negative decimal"
            ) from exc
        if not decimal.is_finite() or decimal < 0:
            raise ValueError(
                f"profile {name!r}: pricing.{field} must be a finite non-negative decimal"
            )
        exponent = decimal.as_tuple().exponent
        if isinstance(exponent, int) and -exponent > _MAX_PRICING_DECIMAL_PLACES:
            raise ValueError(
                f"profile {name!r}: pricing.{field} has more than "
                f"{_MAX_PRICING_DECIMAL_PLACES} decimal places"
            )
        normalized[field] = value.strip()
    return normalized


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
    pricing = _normalize_pricing(name, profile.get("pricing"))
    if pricing is not None:
        normalized["pricing"] = pricing
    if "context_window" in profile:
        context_window = profile["context_window"]
        if type(context_window) is not int or context_window <= 0:
            raise ValueError(f"profile {name!r}: context_window must be a positive integer")
        normalized["context_window"] = context_window
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
