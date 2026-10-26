"""A tool result without its model tool call never reaches the provider."""

from unittest.mock import MagicMock

from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.progena.agent.middleware import ToolResultPairingMiddleware


async def _messages_sent(messages: list) -> list:
    seen: list[ModelRequest] = []

    async def handler(request: ModelRequest):
        seen.append(request)
        return MagicMock()

    await ToolResultPairingMiddleware().awrap_model_call(
        ModelRequest(model=MagicMock(), messages=messages, tools=[]), handler
    )
    return seen[0].messages


async def test_paired_history_is_sent_unchanged():
    messages = [
        HumanMessage("look it up"),
        AIMessage("", tool_calls=[{"name": "search", "args": {}, "id": "call_1"}]),
        ToolMessage("found", tool_call_id="call_1"),
        AIMessage("done"),
    ]

    assert await _messages_sent(messages) == messages


async def test_unpaired_results_are_dropped_and_order_is_kept():
    human = HumanMessage("hi")
    call = AIMessage(
        "",
        tool_calls=[{"name": "search", "args": {}, "id": "call_1"}],
        invalid_tool_calls=[{"name": "broken", "args": "{", "id": "call_2", "error": "bad"}],
    )
    result = ToolMessage("found", tool_call_id="call_1")
    invalid_result = ToolMessage("malformed", tool_call_id="call_2")
    injected = ToolMessage("task finished", tool_call_id="a2a_task-1", id="a2a_task-1")
    answer = AIMessage("ok")

    sent = await _messages_sent([human, injected, call, result, invalid_result, answer])

    assert sent == [human, call, result, invalid_result, answer]


async def test_result_before_its_call_is_dropped():
    # Providers only accept a function output after the call that produced it.
    early = ToolMessage("too early", tool_call_id="call_1")
    call = AIMessage("", tool_calls=[{"name": "search", "args": {}, "id": "call_1"}])
    result = ToolMessage("found", tool_call_id="call_1")

    assert await _messages_sent([early, call, result]) == [call, result]
