"""Real-graph regression test for subagent stream correlation under genuine
LangGraph/deepagents concurrency.

Every other subagent-correlation test in test_deepagent_sse_stream.py drives
DeepAgentSSEStream against a hand-scripted fake graph that replays a fixed
list of stream items -- excellent for exact control over ordering, but it
can't catch a regression in what astream(subgraphs=True) + stream_mode="tasks"
*actually* produce from real deepagents/LangGraph scheduling.

This builds a real deepagents.create_deep_agent() graph with two subagents
dispatched in parallel via the `task` tool, and drives the production
DeepAgentSSEStream against it end-to-end, repeated several times. The
underlying scheduling (ToolNode races parallel tool calls via asyncio.gather)
is an internal implementation detail, not a documented contract, so a flake
here is a real signal worth investigating, not noise to suppress.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from deepagents import create_deep_agent
from deepagents.middleware.subagents import SubAgent
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
    GenericFakeChatModel,
)
from langchain_core.messages import AIMessage

from app.progenitor.agent.middleware import AuditLoggingMiddleware
from app.progenitor.sse.deepagent_stream import DeepAgentSSEStream

REPEAT_COUNT = 10


class _FakeToolCallingModel(FakeMessagesListChatModel):
    """FakeMessagesListChatModel with a no-op bind_tools.

    deepagents unconditionally binds tools onto every model (the main agent
    and each subagent), which the base fake model doesn't implement.
    """

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


class _StreamingFakeModel(GenericFakeChatModel):
    """GenericFakeChatModel with a no-op bind_tools.

    Unlike FakeMessagesListChatModel, this one genuinely implements _stream()
    (word-by-word AIMessageChunk pieces), which nested subagent_message_delta
    handling requires -- a plain non-streaming AIMessage fails the
    isinstance(chunk, AIMessageChunk) guard shared with the top-level handler,
    mirroring how a real streaming-capable model actually behaves in prod.
    """

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


def _make_config() -> SimpleNamespace:
    return SimpleNamespace(llm=SimpleNamespace(reasoning_enabled=False))


def _build_parallel_subagent_graph():
    alpha_model = _StreamingFakeModel(
        messages=iter([AIMessage(content="ALPHA_RESULT_A ALPHA_RESULT_B")])
    )
    beta_model = _StreamingFakeModel(
        messages=iter([AIMessage(content="BETA_RESULT_A BETA_RESULT_B")])
    )
    alpha = SubAgent(
        name="alpha",
        description="alpha test subagent",
        system_prompt="Reply with exactly ALPHA_RESULT.",
        model=alpha_model,
        tools=[],
        middleware=[AuditLoggingMiddleware(log_sink=None)],
    )
    beta = SubAgent(
        name="beta",
        description="beta test subagent",
        system_prompt="Reply with exactly BETA_RESULT.",
        model=beta_model,
        tools=[],
        middleware=[AuditLoggingMiddleware(log_sink=None)],
    )
    main_model = _FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "do alpha work", "subagent_type": "alpha"},
                        "id": "call_alpha",
                    },
                    {
                        "name": "task",
                        "args": {"description": "do beta work", "subagent_type": "beta"},
                        "id": "call_beta",
                    },
                ],
            ),
            AIMessage(content="All done."),
        ]
    )
    return create_deep_agent(
        model=main_model,
        system_prompt="test",
        subagents=[alpha, beta],
        tools=[],
        middleware=[AuditLoggingMiddleware(log_sink=None)],
    )


@pytest.mark.slow
@pytest.mark.asyncio
async def test_real_graph_parallel_subagent_dispatch_correlates_correctly():
    for run_idx in range(REPEAT_COUNT):
        graph = _build_parallel_subagent_graph()
        bridge = DeepAgentSSEStream(graph, config=_make_config(), state={})

        items = [
            item
            async for item in bridge.stream(
                "please delegate to both subagents", thread_id=f"thread-{run_idx}"
            )
        ]

        deltas = [item for item in items if item.get("event_type") == "subagent_message_delta"]
        by_call: dict[str, str] = {}
        for delta in deltas:
            tool_call_id = delta["metadata"]["tool_call_id"]
            by_call[tool_call_id] = by_call.get(tool_call_id, "") + delta["content"]

        assert by_call == {
            "call_alpha": "ALPHA_RESULT_A ALPHA_RESULT_B",
            "call_beta": "BETA_RESULT_A BETA_RESULT_B",
        }, f"run {run_idx}: cross-contamination or missing attribution: {by_call!r}"
        # Each subagent's own reply streamed as more than one chunk, so this
        # exercises accumulation across nested events, not just a single blob.
        assert len(deltas) > 2, f"run {run_idx}: expected multi-chunk streaming, got {deltas!r}"

        starts = {
            item["metadata"]["tool_call_id"]: item["metadata"]["subagent_name"]
            for item in items
            if item.get("event_type") == "subagent_start"
        }
        assert starts == {"call_alpha": "alpha", "call_beta": "beta"}, (
            f"run {run_idx}: unexpected subagent_start attribution: {starts!r}"
        )

        results = {
            item["metadata"]["tool_call_id"]: item["metadata"]["success"]
            for item in items
            if item.get("event_type") == "subagent_result"
        }
        assert results == {"call_alpha": True, "call_beta": True}, (
            f"run {run_idx}: unexpected subagent_result attribution: {results!r}"
        )
