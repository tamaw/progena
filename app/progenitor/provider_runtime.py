"""Per-turn resolution of persisted Responses API providers.

The provider repository is synchronous because it is also used by the backend.
Runtime callers resolve it in a worker thread and retain the resulting immutable
connection snapshot for the rest of a turn.  That makes a Settings change apply
to the next turn without mixing credentials or models inside a running turn.
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace

from .config import LLMConfig
from .providers import GO_RESPONSES_MODELS, get_provider_repository


class ProviderNotConfiguredError(RuntimeError):
    """Raised only when a model call needs a provider that is not ready."""


class ProviderResolutionError(RuntimeError):
    """Raised for a configured provider whose usable connection cannot be resolved."""


def _validated_override(config: LLMConfig, model_override: str | None) -> LLMConfig:
    if model_override is None:
        return config
    model = model_override.strip() if isinstance(model_override, str) else ""
    if not model:
        raise ProviderResolutionError("Model override must be a non-empty string")
    if config.provider_kind == "opencode_go" and model not in GO_RESPONSES_MODELS:
        raise ProviderResolutionError("OpenCode Go model is not supported by the Responses API")
    return replace(config, model_default=model)


async def resolve_provider_config(
    template: LLMConfig | None = None,
    *,
    provider_id: str | None = None,
    model_override: str | None = None,
) -> LLMConfig:
    """Resolve a private provider connection without exposing its credential.

    ``template`` only supplies runtime model behaviour such as reasoning.  Its
    connection fields never act as an environment-style fallback.
    """
    template = template or LLMConfig()
    selected_id = provider_id if provider_id is not None else template.provider_id
    try:
        repository = get_provider_repository()
        record = await asyncio.to_thread(repository.resolve, selected_id, None)
    except LookupError as exc:
        raise ProviderNotConfiguredError(
            "No model provider is configured. Add and select a provider in Settings."
        ) from exc
    except ValueError as exc:
        raise ProviderResolutionError("The selected provider is not ready for model calls") from exc
    except RuntimeError as exc:
        raise ProviderNotConfiguredError(
            "Model providers are unavailable. Configure a provider in Settings."
        ) from exc

    config = LLMConfig.from_provider(
        record,
        reasoning_enabled=template.reasoning_enabled,
        reasoning_effort=template.reasoning_effort,
        reasoning_summary=template.reasoning_summary,
        timeout_seconds=template.timeout_seconds,
        max_retries=template.max_retries,
    )
    return _validated_override(config, model_override)


@dataclass
class ProviderTurn:
    """Connection snapshots selected for one logical model turn."""

    template: LLMConfig
    session_id: str
    _connections: dict[str | None, LLMConfig]
    _resolution_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    @classmethod
    async def create(
        cls,
        template: LLMConfig | None = None,
        *,
        session_id: str = "",
        provider_id: str | None = None,
        resolve_default: bool = True,
    ) -> ProviderTurn:
        turn = cls(template=template or LLMConfig(), session_id=session_id, _connections={})
        # Chat fails early; deterministic routines need no model connection.
        if resolve_default:
            await turn.resolve(provider_id=provider_id)
        return turn

    async def resolve(
        self,
        *,
        provider_id: str | None = None,
        model_override: str | None = None,
        template: LLMConfig | None = None,
    ) -> LLMConfig:
        selected_id = provider_id
        if selected_id is None and template is not None:
            selected_id = template.provider_id
        if selected_id is None:
            selected_id = self.template.provider_id
        behavior = template or self.template
        base = self._connections.get(selected_id)
        resolved_for_behavior = False
        if base is None:
            async with self._resolution_lock:
                base = self._connections.get(selected_id)
                if base is None:
                    resolved = await resolve_provider_config(behavior, provider_id=selected_id)
                    canonical_id = resolved.provider_id
                    base = self._connections.get(canonical_id)
                    if base is None:
                        base = resolved
                        self._connections[canonical_id] = base
                        resolved_for_behavior = True
                    self._connections[selected_id] = base
        if not resolved_for_behavior:
            base = replace(
                base,
                reasoning_enabled=behavior.reasoning_enabled,
                reasoning_effort=behavior.reasoning_effort,
                reasoning_summary=behavior.reasoning_summary,
                timeout_seconds=behavior.timeout_seconds,
                max_retries=behavior.max_retries,
            )
        return _validated_override(base, model_override)


_provider_turn: contextvars.ContextVar[ProviderTurn | None] = contextvars.ContextVar(
    "progenitor_provider_turn", default=None
)


@contextmanager
def activate_provider_turn(turn: ProviderTurn) -> Iterator[None]:
    """Install one immutable provider snapshot for this task and its children."""
    token = _provider_turn.set(turn)
    try:
        yield
    finally:
        _provider_turn.reset(token)


def current_provider_turn() -> ProviderTurn | None:
    """Return the active turn snapshot, if a graph invocation established one."""
    return _provider_turn.get()


async def provider_config_for_call(
    template: LLMConfig | None = None,
    *,
    provider_id: str | None = None,
    model_override: str | None = None,
) -> LLMConfig:
    """Resolve a connection, reusing the active turn snapshot when present."""
    turn = current_provider_turn()
    if turn is not None:
        return await turn.resolve(
            provider_id=provider_id,
            model_override=model_override,
            template=template,
        )
    return await resolve_provider_config(
        template,
        provider_id=provider_id,
        model_override=model_override,
    )
