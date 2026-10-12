"""Validation and defaults for the virtual ``/config/profiles.json`` document."""

from __future__ import annotations

import json
import os
from typing import Any

MODEL_PROFILES_DOC_KEY = "profile"
AZURE_OPENAI_PROVIDER = "azure_openai"


def default_model_profiles_payload() -> dict[str, Any]:
    """Return the small, environment-seeded initial model profile map."""
    return {
        "general": {
            "provider": AZURE_OPENAI_PROVIDER,
            "deployment": os.getenv("PROGENITOR_MODEL_DEFAULT", "gpt-chat-latest"),
        }
    }


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
        normalized[name.strip()] = {
            **profile,
            "provider": provider,
            "deployment": deployment.strip(),
        }
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
    return create_langchain_model(replace(LLMConfig.from_env(), **overrides))
