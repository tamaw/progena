"""Validation and defaults for the virtual ``/config/profiles.json`` document.

The sole built-in estimate is Azure OpenAI GPT-5 mini **Global Standard** USD
pricing: input ``0.25``, cached input ``0.025``, and output ``2.00`` per 1M
tokens. It was verified 2026-10-20 from Azure's primary pricing page,
https://azure.microsoft.com/en-us/pricing/details/azure-openai/; its exact
``data-amount`` values are used rather than the page's rounded ``$0.03``
cached-input display. It does not apply to Data Zone, Regional, Priority,
Batch, PTU, reserved, or negotiated offers. Azure deployment names do not
identify their model or tier, so seeding requires explicit model and tier
environment mappings and never changes an existing catalog.
"""

from __future__ import annotations

import json
import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any

MODEL_PROFILES_DOC_KEY = "profile"
AZURE_OPENAI_PROVIDER = "azure_openai"
_PRICING_RATE_FIELDS = (
    "input_per_million",
    "cached_input_per_million",
    "output_per_million",
)
_PRICING_FIELDS = ("currency", *_PRICING_RATE_FIELDS)
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_DECIMAL_STRING_PATTERN = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d+)?$")
_MAX_PRICING_DECIMAL_PLACES = 12
_DEFAULT_MODEL_PRICING = {
    # Azure OpenAI GPT-5 mini Global Standard, USD per 1M tokens.
    "gpt-5-mini": {
        "currency": "USD",
        "input_per_million": "0.25",
        "cached_input_per_million": "0.025",
        "output_per_million": "2.00",
    }
}
_GLOBAL_STANDARD_TIER = "global_standard"


def _default_pricing_for_configured_model() -> dict[str, str] | None:
    """Return an estimate only for explicit supported model and tier mappings."""
    model = os.getenv("PROGENITOR_MODEL_DEFAULT_MODEL", "").strip().lower()
    tier = os.getenv("PROGENITOR_MODEL_DEFAULT_PRICING_TIER", "").strip().lower()
    if tier != _GLOBAL_STANDARD_TIER:
        return None
    pricing = _DEFAULT_MODEL_PRICING.get(model)
    return dict(pricing) if pricing is not None else None


def default_model_profiles_payload() -> dict[str, Any]:
    """Return the small, environment-seeded initial model profile map."""
    profile: dict[str, Any] = {
        "provider": AZURE_OPENAI_PROVIDER,
        "deployment": os.getenv("PROGENITOR_MODEL_DEFAULT", "gpt-chat-latest"),
    }
    if pricing := _default_pricing_for_configured_model():
        profile["pricing"] = pricing
    return {"general": profile}


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


def validate_model_profiles_payload(payload: Any) -> dict[str, Any]:
    """Validate profile names and the provider/deployment needed for routing."""
    if not isinstance(payload, dict):
        raise ValueError("profile document must be a JSON object")
    normalized: dict[str, Any] = {}
    for name, profile in payload.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("profile names must be non-empty strings")
        if not isinstance(profile, dict):
            raise ValueError(f"profile {name!r} must be an object")
        provider = profile.get("provider")
        deployment = profile.get("deployment")
        if provider != AZURE_OPENAI_PROVIDER:
            raise ValueError(f"profile {name!r}: provider must be {AZURE_OPENAI_PROVIDER!r}")
        if not isinstance(deployment, str) or not deployment.strip():
            raise ValueError(f"profile {name!r}: deployment must be a non-empty string")
        stripped = name.strip()
        if stripped in normalized:
            raise ValueError(f"ambiguous profile names: {stripped!r} appears more than once")
        normalized_profile = {key: value for key, value in profile.items() if key != "pricing"}
        normalized_profile.update(
            provider=provider,
            deployment=deployment.strip(),
        )
        pricing = _normalize_pricing(name, profile.get("pricing"))
        if pricing is not None:
            normalized_profile["pricing"] = pricing
        normalized[stripped] = normalized_profile
    return normalized


def validate_model_profiles_document(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"profiles.json must be valid JSON: {exc}") from exc
    return validate_model_profiles_payload(payload)


async def resolve_model_profile(state_manager: Any, name: str) -> Any:
    """Resolve the current named profile using the configured Azure adapter."""
    from dataclasses import replace

    from ..agent.adapters import create_langchain_model
    from ..config import LLMConfig

    profiles = validate_model_profiles_payload(await state_manager.get_model_profiles_config())
    if name not in profiles:
        raise ValueError(f"Unknown model profile {name!r}")
    profile = profiles[name]
    overrides = {"model_default": profile["deployment"]}
    for field in ("reasoning_enabled", "reasoning_effort", "reasoning_summary"):
        if field in profile:
            value = profile[field]
            if field == "reasoning_enabled":
                if not isinstance(value, bool):
                    raise ValueError(f"profile {name!r}: reasoning_enabled must be boolean")
            elif not isinstance(value, str) or not value.strip():
                raise ValueError(f"profile {name!r}: {field} must be a non-empty string")
            overrides[field] = value
    return create_langchain_model(
        replace(LLMConfig.from_env(), **overrides),
        profile_name=name,
    )
