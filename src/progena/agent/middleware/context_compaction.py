"""Persist honest context-footprint associations for native conversation compaction."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import BaseMessage

from src.progena.sessions.messages import (
    coerce_messages_list,
    extract_context_usage,
    get_last_ai_message,
)

_NATIVE_SUMMARIZATION_EVENT_KEY = "_summarization_event"
_STATE_KEY = "_context_compaction"
_MESSAGE_KEY = "progena_context_compaction"
_MEASUREMENT = "comparable_provider_footprints"


def _event_boundary(event: Any) -> tuple[int, int] | None:
    if not isinstance(event, Mapping):
        return None
    created_at_index = event.get("created_at_index")
    cutoff_index = event.get("cutoff_index")
    if (
        type(created_at_index) is not int
        or created_at_index < 0
        or type(cutoff_index) is not int
        or cutoff_index < 0
    ):
        return None
    return created_at_index, cutoff_index


def _stored_descriptor(value: Any) -> dict[str, int | str] | None:
    if not isinstance(value, Mapping):
        return None
    before = value.get("before_input_tokens")
    after = value.get("after_input_tokens")
    freed = value.get("freed_input_tokens")
    if (
        type(before) is not int
        or before < 0
        or type(after) is not int
        or after < 0
        or type(freed) is not int
        or freed <= 0
        or freed != before - after
        or value.get("measurement") != _MEASUREMENT
    ):
        return None
    return {
        "before_input_tokens": before,
        "after_input_tokens": after,
        "freed_input_tokens": freed,
        "measurement": _MEASUREMENT,
    }


def _comparison_descriptor(before_message: Any, after_message: Any) -> dict[str, int | str] | None:
    """Compare complete provider footprints without claiming tokenizer precision.

    The two provider calls may have different current-turn additions, tools, or
    system prompt revisions.  Only a positive decrease is useful to surface,
    and it is explicitly marked as a comparable-footprints measurement rather
    than a count of exactly which native summary tokens were removed.
    """
    before_usage = extract_context_usage(before_message)
    after_usage = extract_context_usage(after_message)
    if before_usage is None or after_usage is None:
        return None
    if (
        type(before_usage.get("provider_id")) is not str
        or not before_usage["provider_id"]
        or type(before_usage.get("deployment")) is not str
        or not before_usage["deployment"]
        or before_usage["provider_id"] != after_usage.get("provider_id")
        or before_usage["deployment"] != after_usage.get("deployment")
    ):
        return None
    before = before_usage["input_tokens"]
    after = after_usage["input_tokens"]
    if type(before) is not int or type(after) is not int or after >= before:
        return None
    return {
        "before_input_tokens": before,
        "after_input_tokens": after,
        "freed_input_tokens": before - after,
        "measurement": _MEASUREMENT,
    }


def _with_descriptor(message: BaseMessage, descriptor: dict[str, int | str]) -> BaseMessage:
    metadata = dict(message.response_metadata)
    if metadata.get(_MESSAGE_KEY) == descriptor:
        return message
    metadata[_MESSAGE_KEY] = descriptor
    return message.model_copy(update={"response_metadata": metadata})


def _without_descriptor(message: BaseMessage) -> BaseMessage:
    if _MESSAGE_KEY not in message.response_metadata:
        return message
    metadata = dict(message.response_metadata)
    metadata.pop(_MESSAGE_KEY, None)
    return message.model_copy(update={"response_metadata": metadata})


def _replace_response_message(
    response: ModelResponse[Any], descriptor: dict[str, int | str]
) -> ModelResponse[Any]:
    result = list(response.result)
    for index in range(len(result) - 1, -1, -1):
        message = result[index]
        if not isinstance(message, BaseMessage) or extract_context_usage(message) is None:
            continue
        result[index] = _with_descriptor(message, descriptor)
        return replace(response, result=result)
    return response


class ContextCompactionMiddleware(AgentMiddleware):
    """Carry the latest root compaction descriptor on provider-footprint messages.

    Native DeepAgents commits ``_summarization_event`` with the model response.
    This after-model hook runs in the following graph node, persists the first
    descriptor-bearing response as a normal messages-channel update, and keeps
    the descriptor on later root provider responses.  That makes the latest
    history projection reconstructable without querying checkpoint history.
    """

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        response = await handler(request)
        descriptor = _stored_descriptor(request.state.get(_STATE_KEY))
        return _replace_response_message(response, descriptor) if descriptor else response

    async def aafter_model(self, state: AgentState[Any], runtime: Any) -> dict[str, Any] | None:
        del runtime
        event_boundary = _event_boundary(state.get(_NATIVE_SUMMARIZATION_EVENT_KEY))
        if event_boundary is None:
            return None

        messages = coerce_messages_list(state.get("messages"))
        created_at_index, _cutoff_index = event_boundary
        # A fresh native event and its response share one model step. Refuse to
        # backfill an event from an older checkpoint after later tool/model
        # loops; those messages may use different prompt composition.
        if created_at_index != len(messages) - 1:
            return None
        target = messages[created_at_index]
        if not isinstance(target, BaseMessage):
            return None

        descriptor = _comparison_descriptor(
            get_last_ai_message(messages[:created_at_index]), target
        )
        if descriptor is None:
            cleared = _without_descriptor(target)
            update: dict[str, Any] = {_STATE_KEY: None}
            if cleared is not target:
                update["messages"] = [cleared]
            return update
        return {
            "messages": [_with_descriptor(target, descriptor)],
            _STATE_KEY: descriptor,
        }
