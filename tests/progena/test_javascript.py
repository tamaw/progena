"""Behavioral boundaries for Progena's computation-only JavaScript tool."""

from collections.abc import Sequence
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import InMemorySaver

from src.progena.agent.factory import _create_javascript_middleware


class _ToolCallingModel(FakeMessagesListChatModel):
    """Scripted model that accepts the graph's dynamically supplied tools."""

    def bind_tools(self, tools: Sequence[BaseTool], **kwargs):
        return self


def _javascript_call(code: str, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "javascript", "args": {"code": code}, "id": call_id}],
    )


def _graph_for(*codes: str, tools: Sequence[BaseTool] = ()):
    responses = [_javascript_call(code, f"javascript-{index}") for index, code in enumerate(codes)]
    responses.append(AIMessage(content="complete"))
    return create_agent(
        _ToolCallingModel(responses=responses),
        tools=list(tools),
        middleware=[_create_javascript_middleware()],
    )


def _tool_results(result: dict[str, Any]) -> list[str]:
    results: list[str] = []
    for message in result["messages"]:
        if isinstance(message, ToolMessage):
            assert isinstance(message.content, str)
            results.append(message.content)
    return results


def test_javascript_has_no_host_or_parent_tool_access() -> None:
    @tool
    def parent_secret() -> str:
        """A parent tool which must not become a JavaScript host API."""
        return "secret"

    graph = _graph_for(
        "JSON.stringify([typeof process, typeof require, typeof fetch, typeof Deno, "
        "typeof fs, typeof tools, typeof task])",
        tools=[parent_secret],
    )

    result = graph.invoke({"messages": [HumanMessage(content="inspect runtime")]})

    assert _tool_results(result) == [
        '<result>["undefined","undefined","undefined","undefined","undefined",'
        '"undefined","undefined"]</result>'
    ]


async def test_javascript_reuses_turn_globals_but_isolates_new_turns_and_threads() -> None:
    graph = create_agent(
        _ToolCallingModel(
            responses=[
                _javascript_call("globalThis.previousCall = 17", "javascript-set"),
                _javascript_call("previousCall + 1", "javascript-reuse"),
                AIMessage(content="set"),
                _javascript_call("typeof previousCall", "javascript-read"),
                AIMessage(content="read"),
                _javascript_call("typeof previousCall", "javascript-resume"),
                AIMessage(content="resumed"),
            ]
        ),
        tools=[],
        middleware=[_create_javascript_middleware()],
        checkpointer=InMemorySaver(),
    )

    first = await graph.ainvoke(
        {"messages": [HumanMessage(content="set a global")]},
        {"configurable": {"thread_id": "javascript-first"}},
    )
    second = await graph.ainvoke(
        {"messages": [HumanMessage(content="inspect a new thread")]},
        {"configurable": {"thread_id": "javascript-second"}},
    )
    resumed = await graph.ainvoke(
        {"messages": [HumanMessage(content="inspect the original thread in a new turn")]},
        {"configurable": {"thread_id": "javascript-first"}},
    )

    assert _tool_results(first) == ["<result>17</result>", "<result>18</result>"]
    assert _tool_results(second) == ["<result>undefined</result>"]
    assert _tool_results(resumed)[-1] == "<result>undefined</result>"


def test_javascript_recovers_after_timeout_and_heap_exhaustion() -> None:
    graph = _graph_for(
        "while (true) {}",
        "6 * 7",
        "new ArrayBuffer(128 * 1024 * 1024)",
        "21 * 2",
    )

    result = graph.invoke({"messages": [HumanMessage(content="exercise limits")]})

    timeout, after_timeout, exhausted, after_exhaustion = _tool_results(result)
    assert 'type="Timeout"' in timeout
    assert after_timeout == "<result>42</result>"
    assert 'type="OutOfMemory"' in exhausted
    assert after_exhaustion == "<result>42</result>"


def test_javascript_truncates_console_and_result_output() -> None:
    graph = _graph_for("console.log('x'.repeat(5000)); 'y'.repeat(5000)")

    result = _tool_results(graph.invoke({"messages": [HumanMessage(content="produce output")]}))[0]

    assert result.startswith("<stdout>\n")
    assert "</stdout>\n<result>" in result
    assert result.count("… [truncated ") == 2
    assert 0 < result.count("x") < 5000
    assert 0 < result.count("y") < 5000
