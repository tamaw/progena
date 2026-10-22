"""Shared domain contracts and helpers."""

from __future__ import annotations

from typing import Any

from src.progenitor.settings.model_profiles import validate_model_profiles_payload


async def resolve_model_profile(state_manager: Any, name: str) -> Any:
    """Return a profile-bound model whose connection resolves from Postgres."""
    from src.progenitor.agent.adapters import ProviderChatModel
    from src.progenitor.config import LLMConfig

    profiles = validate_model_profiles_payload(await state_manager.get_model_profiles_config())
    if name not in profiles:
        raise ValueError(f"Unknown model profile {name!r}")
    profile = profiles[name]
    defaults = LLMConfig.from_env()
    template = LLMConfig(
        reasoning_enabled=profile.get("reasoning_enabled", True),
        reasoning_effort=profile.get("reasoning_effort", LLMConfig.reasoning_effort),
        reasoning_summary=profile.get("reasoning_summary", LLMConfig.reasoning_summary),
        timeout_seconds=defaults.timeout_seconds,
        max_retries=defaults.max_retries,
    )
    return ProviderChatModel(
        template=template,
        provider_id=profile["provider_id"],
        model_override=profile["deployment"],
        profile_name=name,
    )
