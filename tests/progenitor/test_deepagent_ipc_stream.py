"""Tests for converting native DeepAgent/LangGraph streams to IPC items."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from app.progenitor.ipc.deepagent_stream import DeepAgentIPCStream
from app.progenitor.runtime.messages import extract_reasoning_trace


class _FakeGraph:
    def __init__(self, items):
        self.items = items
        self.last_kwargs = None

    async def astream(self, *args, **kwargs):
        self.last_kwargs = kwargs
        for item in self.items:
            yield item


def _make_config(*, reasoning_enabled: bool = False):
    return SimpleNamespace(llm=SimpleNamespace(reasoning_enabled=reasoning_enabled))


async def _collect_items(bridge: DeepAgentIPCStream, message: str = "hello") -> list[dict]:
    return [item async for item in bridge.stream(message, thread_id="thread-1")]


@pytest.mark.asyncio
async def test_graph_message_events_stream_incrementally_without_duplicate_final():
    graph = _FakeGraph(
        [
            ("messages", (AIMessageChunk(content="Hel"), {"langgraph_node": "respond"})),
            ("messages", (AIMessageChunk(content="lo"), {"langgraph_node": "respond"})),
            (
                "updates",
                {
                    "respond": {
                        "pending_response": "Hello",
                        "messages": [AIMessage(content="Hello")],
                    }
                },
            ),
        ]
    )
    bridge = DeepAgentIPCStream(graph, config=_make_config(), state={})

    items = await _collect_items(bridge)

    assert graph.last_kwargs["stream_mode"] == ["messages", "updates", "custom", "tasks"]
    assert [item for item in items if item["type"] == "stream_chunk"] == [
        {"type": "stream_chunk", "content": "Hel", "is_final": False},
        {"type": "stream_chunk", "content": "lo", "is_final": False},
        {"type": "stream_chunk", "content": "", "is_final": True},
    ]
    assert bridge.last_response == "Hello"


@pytest.mark.asyncio
async def test_pending_response_still_streams_when_no_message_events_exist():
    graph = _FakeGraph(
        [
            (
                "updates",
                {
                    "respond": {
                        "pending_response": "Hello",
                        "messages": [AIMessage(content="Hello")],
                    }
                },
            ),
        ]
    )
    bridge = DeepAgentIPCStream(graph, config=_make_config(), state={})

    items = await _collect_items(bridge)

    assert [item for item in items if item["type"] == "stream_chunk"] == [
        {"type": "stream_chunk", "content": "Hello", "is_final": False},
        {"type": "stream_chunk", "content": "", "is_final": True},
    ]


@pytest.mark.asyncio
async def test_stream_items_include_reasoning_and_custom_events():
    graph = _FakeGraph(
        [
            (
                "messages",
                (
                    AIMessageChunk(
                        content=[
                            {
                                "type": "reasoning",
                                "summary": [
                                    {"type": "summary_text", "text": "Checked constraints. "}
                                ],
                            },
                            {"type": "text", "text": "Hi"},
                        ]
                    ),
                    {"langgraph_node": "respond"},
                ),
            ),
            {"custom_node": {"messages": [AIMessage(content="Hi")]}},
            (
                "custom",
                {
                    "event_type": "tool_start",
                    "content": "search",
                    "metadata": {"tool_name": "search"},
                },
            ),
        ]
    )
    bridge = DeepAgentIPCStream(graph, config=_make_config(), state={})

    items = await _collect_items(bridge)

    assert {
        "type": "stream_event",
        "event_type": "reasoning_delta",
        "content": "Checked constraints. ",
        "metadata": {
            "message_chunk_type": "AIMessageChunk",
            "stream_metadata": {"langgraph_node": "respond"},
            "text_length": 21,
        },
    } in items
    assert {
        "type": "stream_event",
        "event_type": "tool_start",
        "content": "search",
        "metadata": {"tool_name": "search"},
    } in items
    assert {"type": "stream_chunk", "content": "Hi", "is_final": False} in items


@pytest.mark.asyncio
async def test_todos_in_node_output_stream_as_todos_update_event():
    todos = [
        {"content": "Investigate the bug", "status": "completed"},
        {"content": "Write the fix", "status": "in_progress"},
        {"content": "Add a regression test", "status": "pending"},
    ]
    graph = _FakeGraph(
        [
            (
                "updates",
                {
                    "tools": {
                        "todos": todos,
                        "messages": [AIMessage(content="")],
                    }
                },
            ),
        ]
    )
    bridge = DeepAgentIPCStream(graph, config=_make_config(), state={})

    items = await _collect_items(bridge)

    assert {
        "type": "stream_event",
        "event_type": "todos_update",
        "content": "",
        "metadata": {"todos": todos},
    } in items
    assert bridge.state["todos"] == todos


@pytest.mark.asyncio
async def test_node_output_without_todos_emits_no_todos_update_event():
    graph = _FakeGraph(
        [
            (
                "updates",
                {
                    "respond": {
                        "pending_response": "Hello",
                        "messages": [AIMessage(content="Hello")],
                    }
                },
            ),
        ]
    )
    bridge = DeepAgentIPCStream(graph, config=_make_config(), state={})

    items = await _collect_items(bridge)

    assert not any(item.get("event_type") == "todos_update" for item in items)


@pytest.mark.asyncio
async def test_task_tool_events_expand_into_subagent_events():
    graph = _FakeGraph(
        [
            (
                "custom",
                {
                    "event_type": "tool_start",
                    "content": "task",
                    "metadata": {
                        "tool_name": "task",
                        "tool_call_id": "call_1",
                        "subagent_type": "hello-world",
                        "args_preview": {"subagent_type": "hello-world"},
                    },
                },
            ),
            (
                "custom",
                {
                    "event_type": "tool_result",
                    "content": "task",
                    "metadata": {
                        "tool_name": "task",
                        "tool_call_id": "call_1",
                        "duration_ms": 12.5,
                        "success": True,
                        "result_preview": "HELLO_FROM_SUBAGENT",
                    },
                },
            ),
            ("updates", {"respond": {"pending_response": "HELLO_FROM_SUBAGENT"}}),
        ]
    )
    bridge = DeepAgentIPCStream(graph, config=_make_config(), state={})

    items = await _collect_items(bridge)

    assert {
        "type": "stream_event",
        "event_type": "subagent_start",
        "content": "hello-world",
        "metadata": {
            "subagent_name": "hello-world",
            "tool_call_id": "call_1",
        },
    } in items
    assert {
        "type": "stream_event",
        "event_type": "subagent_result",
        "content": "hello-world",
        "metadata": {
            "subagent_name": "hello-world",
            "tool_call_id": "call_1",
            "success": True,
            "duration_ms": 12.5,
            "result_preview": "HELLO_FROM_SUBAGENT",
        },
    } in items
    assert {"type": "stream_chunk", "content": "HELLO_FROM_SUBAGENT", "is_final": False} in items

    # Regression: the raw generic tool_start/tool_result for the top-level
    # `task` call itself must NOT also go out once it's been successfully
    # translated into subagent_start/subagent_result above -- neither the
    # live frontend's grouping logic nor reload reconstruction recognizes
    # that raw pair, so it used to surface as a redundant, ungrouped
    # "Tool: Task" row sitting right next to the SubagentCard built from the
    # very same event.
    raw_task_events = [
        item
        for item in items
        if item.get("type") == "stream_event"
        and item.get("event_type") in {"tool_start", "tool_result", "tool_error"}
        and item.get("metadata", {}).get("tool_name") == "task"
    ]
    assert raw_task_events == []


@pytest.mark.asyncio
async def test_task_tool_result_images_survive_subagent_result_translation():
    """Regression: the raw tool_result carrying top-level `images` (see
    _handle_custom_stream_event) used to be fully discarded once translated
    into subagent_result -- that dict had no images field of its own, so a
    `task` delegation's own images silently vanished instead of surfacing on
    the resulting subagent_result event the way they did on the raw event
    before translation replaced it."""
    graph = _FakeGraph(
        [
            (
                "custom",
                {
                    "event_type": "tool_start",
                    "content": "task",
                    "metadata": {
                        "tool_name": "task",
                        "tool_call_id": "call_1",
                        "subagent_type": "hello-world",
                        "args_preview": {"subagent_type": "hello-world"},
                    },
                },
            ),
            (
                "custom",
                {
                    "event_type": "tool_result",
                    "content": "task",
                    "metadata": {
                        "tool_name": "task",
                        "tool_call_id": "call_1",
                        "duration_ms": 12.5,
                        "success": True,
                        "result_preview": "HELLO_FROM_SUBAGENT",
                    },
                    "images": [{"id": "img-1", "media_type": "image/png", "base64": "abc123"}],
                },
            ),
        ]
    )
    bridge = DeepAgentIPCStream(graph, config=_make_config(), state={})

    items = await _collect_items(bridge)

    subagent_results = [item for item in items if item.get("event_type") == "subagent_result"]
    assert len(subagent_results) == 1
    assert subagent_results[0]["images"] == [
        {"id": "img-1", "media_type": "image/png", "base64": "abc123"}
    ]


@pytest.mark.asyncio
async def test_task_tool_start_without_subagent_type_falls_back_to_raw_event():
    """If expansion can't determine which subagent this is (malformed/unexpected
    args_preview), the raw generic tool_start must still go out -- better to show
    an unlabeled "Tool: Task" row than silently drop the event entirely."""
    graph = _FakeGraph(
        [
            (
                "custom",
                {
                    "event_type": "tool_start",
                    "content": "task",
                    "metadata": {
                        "tool_name": "task",
                        "tool_call_id": "call_1",
                        "args_preview": {},
                    },
                },
            ),
        ]
    )
    bridge = DeepAgentIPCStream(graph, config=_make_config(), state={})

    items = await _collect_items(bridge)

    assert {
        "type": "stream_event",
        "event_type": "tool_start",
        "content": "task",
        "metadata": {
            "tool_name": "task",
            "tool_call_id": "call_1",
            "args_preview": {},
        },
    } in items
    assert not any(item.get("event_type") == "subagent_start" for item in items)


@pytest.mark.asyncio
async def test_call_agent_tool_events_pass_through_unmodified_alongside_task_expansion():
    """A2A's call_agent tool calls must pass through completely unmodified --
    Progenitor's own act of dispatching/receiving that tool call is an
    ordinary tool call, same as search_web or read_file, and gets no
    dedicated event-type translation the way `task` does above (only
    a2a_call_progress -- the A2A protocol's own status stream, with no
    tool-call equivalent -- is emitted as a dedicated type, directly from
    call_agent.py, not translated after the fact here). Interleaved with a
    `task` call in the same stream to confirm _expand_task_subagent_events'
    tool_name == "task" guard keeps the two calls from interfering with each
    other."""
    graph = _FakeGraph(
        [
            (
                "custom",
                {
                    "event_type": "tool_start",
                    "content": "task",
                    "metadata": {
                        "tool_name": "task",
                        "tool_call_id": "call_1",
                        "subagent_type": "hello-world",
                        "args_preview": {"subagent_type": "hello-world"},
                    },
                },
            ),
            (
                "custom",
                {
                    "event_type": "tool_start",
                    "content": "call_agent",
                    "metadata": {
                        "tool_name": "call_agent",
                        "tool_call_id": "call_9",
                        "args_preview": {"agent": "greetings_agent"},
                    },
                },
            ),
            (
                "custom",
                {
                    "event_type": "tool_result",
                    "content": "task",
                    "metadata": {
                        "tool_name": "task",
                        "tool_call_id": "call_1",
                        "duration_ms": 12.5,
                        "success": True,
                        "result_preview": "HELLO_FROM_SUBAGENT",
                    },
                },
            ),
            (
                "custom",
                {
                    "event_type": "tool_result",
                    "content": "call_agent",
                    "metadata": {
                        "tool_name": "call_agent",
                        "tool_call_id": "call_9",
                        "duration_ms": 5.0,
                        "success": True,
                        "result_preview": "Hello!",
                    },
                },
            ),
        ]
    )
    bridge = DeepAgentIPCStream(graph, config=_make_config(), state={})

    items = await _collect_items(bridge)

    assert {
        "type": "stream_event",
        "event_type": "tool_start",
        "content": "call_agent",
        "metadata": {
            "tool_name": "call_agent",
            "tool_call_id": "call_9",
            "args_preview": {"agent": "greetings_agent"},
        },
    } in items
    assert {
        "type": "stream_event",
        "event_type": "tool_result",
        "content": "call_agent",
        "metadata": {
            "tool_name": "call_agent",
            "tool_call_id": "call_9",
            "duration_ms": 5.0,
            "success": True,
            "result_preview": "Hello!",
        },
    } in items
    event_types = [item.get("event_type") for item in items if item.get("type") == "stream_event"]
    assert "subagent_start" in event_types
    assert "subagent_result" in event_types
    assert "a2a_call_start" not in event_types
    assert "a2a_call_result" not in event_types


@pytest.mark.asyncio
async def test_astream_kwargs_include_subgraphs_when_graph_supports_it():
    graph = _FakeGraph([("updates", {"respond": {"pending_response": "hi"}})])
    bridge = DeepAgentIPCStream(graph, config=_make_config(), state={})

    await _collect_items(bridge)

    assert graph.last_kwargs["subgraphs"] is True


@pytest.mark.asyncio
async def test_nested_subagent_stream_events_expand_into_subagent_tool_events():
    graph = _FakeGraph(
        [
            (
                "custom",
                {
                    "event_type": "tool_start",
                    "content": "task",
                    "metadata": {
                        "tool_name": "task",
                        "tool_call_id": "call_1",
                        "subagent_type": "hello-world",
                        "args_preview": {"subagent_type": "hello-world"},
                    },
                },
            ),
            # tasks-mode start event: the "tools" node's own push task for this
            # dispatch. Its id ("task_1") becomes the nested namespace's own
            # segment suffix ("tools:task_1") -- the causally-exact join key.
            (
                "tasks",
                {
                    "id": "task_1",
                    "name": "tools",
                    "input": {"tool_call": {"id": "call_1", "name": "task", "args": {}}},
                    "triggers": ["branch:to:tools"],
                },
            ),
            (
                ("tools:task_1",),
                "custom",
                {
                    "event_type": "tool_start",
                    "content": "read_file",
                    "metadata": {
                        "tool_name": "read_file",
                        "tool_call_id": "nested_call_1",
                        "args_preview": {"path": "README.md"},
                    },
                },
            ),
            (
                ("tools:task_1",),
                "messages",
                (AIMessageChunk(content="Hel"), {"langgraph_node": "model"}),
            ),
            (
                ("tools:task_1",),
                "messages",
                (AIMessageChunk(content="lo"), {"langgraph_node": "model"}),
            ),
            (
                ("tools:task_1",),
                "custom",
                {
                    "event_type": "tool_result",
                    "content": "read_file",
                    "metadata": {
                        "tool_name": "read_file",
                        "tool_call_id": "nested_call_1",
                        "duration_ms": 4.0,
                        "success": True,
                        "result_preview": "# Title",
                    },
                },
            ),
            (
                "custom",
                {
                    "event_type": "tool_result",
                    "content": "task",
                    "metadata": {
                        "tool_name": "task",
                        "tool_call_id": "call_1",
                        "duration_ms": 20.0,
                        "success": True,
                        "result_preview": "HELLO_FROM_SUBAGENT",
                    },
                },
            ),
            ("updates", {"respond": {"pending_response": "HELLO_FROM_SUBAGENT"}}),
        ]
    )
    bridge = DeepAgentIPCStream(graph, config=_make_config(), state={})

    items = await _collect_items(bridge)

    assert {
        "type": "stream_event",
        "event_type": "subagent_tool_start",
        "content": "read_file",
        "metadata": {
            "tool_call_id": "call_1",
            "subagent_name": "hello-world",
            "nested_tool_call_id": "nested_call_1",
            "nested_tool_name": "read_file",
            "args_preview": {"path": "README.md"},
        },
    } in items
    assert {
        "type": "stream_event",
        "event_type": "subagent_tool_result",
        "content": "read_file",
        "metadata": {
            "tool_call_id": "call_1",
            "subagent_name": "hello-world",
            "nested_tool_call_id": "nested_call_1",
            "nested_tool_name": "read_file",
            "duration_ms": 4.0,
            "success": True,
            "result_preview": "# Title",
        },
    } in items
    assert {
        "type": "stream_event",
        "event_type": "subagent_message_delta",
        "content": "Hel",
        "metadata": {"tool_call_id": "call_1", "subagent_name": "hello-world", "text_length": 3},
    } in items
    assert {
        "type": "stream_event",
        "event_type": "subagent_message_delta",
        "content": "lo",
        "metadata": {"tool_call_id": "call_1", "subagent_name": "hello-world", "text_length": 2},
    } in items
    # Nested tool events must fully replace, never duplicate as top-level events.
    assert not any(
        item.get("event_type") in {"tool_start", "tool_result"}
        and item.get("metadata", {}).get("tool_call_id") == "nested_call_1"
        for item in items
    )


@pytest.mark.asyncio
async def test_namespaced_event_with_no_pending_dispatch_is_dropped_safely():
    graph = _FakeGraph(
        [
            (
                ("tools:orphan",),
                "custom",
                {
                    "event_type": "tool_start",
                    "content": "mystery",
                    "metadata": {"tool_name": "mystery", "tool_call_id": "nested_x"},
                },
            ),
            (
                ("tools:orphan",),
                "messages",
                (AIMessageChunk(content="orphan text"), {}),
            ),
            ("updates", {"respond": {"pending_response": "done"}}),
        ]
    )
    bridge = DeepAgentIPCStream(graph, config=_make_config(), state={})

    items = await _collect_items(bridge)

    assert not any(str(item.get("event_type", "")).startswith("subagent_") for item in items)
    assert {"type": "stream_chunk", "content": "done", "is_final": False} in items


@pytest.mark.asyncio
async def test_parallel_subagent_dispatch_correlates_by_task_id_not_arrival_order():
    # The task_id join must be pure content-based correlation, not disguised
    # order-sensitivity: dispatch order (call_1, call_2), tasks-mode
    # registration order (task_B registered before task_A), and nested-event
    # arrival order (namespace B fully completes before namespace A starts
    # producing anything) are all deliberately made to disagree with each
    # other. Correct attribution must hold regardless -- this replaces an
    # earlier design that keyed off first-seen-namespace order, which was
    # empirically found to mismatch dispatch order ~50% of the time under
    # genuine concurrent async subagent execution.
    ns_a = ("tools:task_A",)
    ns_b = ("tools:task_B",)

    graph = _FakeGraph(
        [
            (
                "custom",
                {
                    "event_type": "tool_start",
                    "content": "task",
                    "metadata": {
                        "tool_name": "task",
                        "tool_call_id": "call_1",
                        "subagent_type": "code-reviewer",
                        "args_preview": {"subagent_type": "code-reviewer"},
                    },
                },
            ),
            (
                "custom",
                {
                    "event_type": "tool_start",
                    "content": "task",
                    "metadata": {
                        "tool_name": "task",
                        "tool_call_id": "call_2",
                        "subagent_type": "researcher",
                        "args_preview": {"subagent_type": "researcher"},
                    },
                },
            ),
            # tasks-mode registration deliberately reversed vs. dispatch order.
            (
                "tasks",
                {
                    "id": "task_B",
                    "name": "tools",
                    "input": {"tool_call": {"id": "call_2", "name": "task", "args": {}}},
                    "triggers": ["branch:to:tools"],
                },
            ),
            (
                "tasks",
                {
                    "id": "task_A",
                    "name": "tools",
                    "input": {"tool_call": {"id": "call_1", "name": "task", "args": {}}},
                    "triggers": ["branch:to:tools"],
                },
            ),
            # namespace B runs to completion before namespace A produces anything.
            (
                ns_b,
                "custom",
                {
                    "event_type": "tool_start",
                    "content": "grep",
                    "metadata": {"tool_name": "grep", "tool_call_id": "nested_b1"},
                },
            ),
            (ns_b, "messages", (AIMessageChunk(content="b-chunk"), {})),
            (
                ns_b,
                "custom",
                {
                    "event_type": "tool_result",
                    "content": "grep",
                    "metadata": {
                        "tool_name": "grep",
                        "tool_call_id": "nested_b1",
                        "duration_ms": 1.0,
                        "success": True,
                        "result_preview": "match",
                    },
                },
            ),
            (
                "custom",
                {
                    "event_type": "tool_result",
                    "content": "task",
                    "metadata": {
                        "tool_name": "task",
                        "tool_call_id": "call_2",
                        "duration_ms": 5.0,
                        "success": True,
                        "result_preview": "research done",
                    },
                },
            ),
            # namespace A only starts after B has fully finished.
            (
                ns_a,
                "custom",
                {
                    "event_type": "tool_start",
                    "content": "read_file",
                    "metadata": {"tool_name": "read_file", "tool_call_id": "nested_a1"},
                },
            ),
            (ns_a, "messages", (AIMessageChunk(content="a-chunk"), {})),
            (
                ns_a,
                "custom",
                {
                    "event_type": "tool_result",
                    "content": "read_file",
                    "metadata": {
                        "tool_name": "read_file",
                        "tool_call_id": "nested_a1",
                        "duration_ms": 3.0,
                        "success": True,
                        "result_preview": "contents",
                    },
                },
            ),
            (
                "custom",
                {
                    "event_type": "tool_result",
                    "content": "task",
                    "metadata": {
                        "tool_name": "task",
                        "tool_call_id": "call_1",
                        "duration_ms": 9.0,
                        "success": True,
                        "result_preview": "review done",
                    },
                },
            ),
            ("updates", {"respond": {"pending_response": "All done."}}),
        ]
    )
    bridge = DeepAgentIPCStream(graph, config=_make_config(), state={})

    items = await _collect_items(bridge)

    def _metadata_for(event_type: str, nested_tool_call_id: str) -> dict:
        matches = [
            item
            for item in items
            if item.get("event_type") == event_type
            and item.get("metadata", {}).get("nested_tool_call_id") == nested_tool_call_id
        ]
        assert len(matches) == 1, f"expected exactly one {event_type} for {nested_tool_call_id}"
        return matches[0]["metadata"]

    # ns_a (dispatched first, but arrives on the stream LAST) still binds to call_1/code-reviewer.
    assert _metadata_for("subagent_tool_start", "nested_a1")["tool_call_id"] == "call_1"
    assert _metadata_for("subagent_tool_start", "nested_a1")["subagent_name"] == "code-reviewer"
    assert _metadata_for("subagent_tool_result", "nested_a1")["tool_call_id"] == "call_1"

    # ns_b (dispatched second, but arrives on the stream FIRST) still binds to call_2/researcher.
    assert _metadata_for("subagent_tool_start", "nested_b1")["tool_call_id"] == "call_2"
    assert _metadata_for("subagent_tool_start", "nested_b1")["subagent_name"] == "researcher"
    assert _metadata_for("subagent_tool_result", "nested_b1")["tool_call_id"] == "call_2"

    deltas = [item for item in items if item.get("event_type") == "subagent_message_delta"]
    a_deltas = [d for d in deltas if d["metadata"]["tool_call_id"] == "call_1"]
    b_deltas = [d for d in deltas if d["metadata"]["tool_call_id"] == "call_2"]
    assert [d["content"] for d in a_deltas] == ["a-chunk"]
    assert [d["content"] for d in b_deltas] == ["b-chunk"]


@pytest.mark.asyncio
async def test_parallel_dispatch_of_same_subagent_type_does_not_cross_contaminate():
    # The hardest case: two concurrent dispatches of the SAME subagent type,
    # where subagent_name alone can never disambiguate -- only the exact
    # task_id join can.
    ns_first = ("tools:task_X",)
    ns_second = ("tools:task_Y",)

    graph = _FakeGraph(
        [
            (
                "custom",
                {
                    "event_type": "tool_start",
                    "content": "task",
                    "metadata": {
                        "tool_name": "task",
                        "tool_call_id": "call_x",
                        "subagent_type": "researcher",
                        "args_preview": {"subagent_type": "researcher"},
                    },
                },
            ),
            (
                "custom",
                {
                    "event_type": "tool_start",
                    "content": "task",
                    "metadata": {
                        "tool_name": "task",
                        "tool_call_id": "call_y",
                        "subagent_type": "researcher",
                        "args_preview": {"subagent_type": "researcher"},
                    },
                },
            ),
            (
                "tasks",
                {
                    "id": "task_X",
                    "name": "tools",
                    "input": {"tool_call": {"id": "call_x", "name": "task", "args": {}}},
                    "triggers": ["branch:to:tools"],
                },
            ),
            (
                "tasks",
                {
                    "id": "task_Y",
                    "name": "tools",
                    "input": {"tool_call": {"id": "call_y", "name": "task", "args": {}}},
                    "triggers": ["branch:to:tools"],
                },
            ),
            (ns_second, "messages", (AIMessageChunk(content="Y-text"), {})),
            (ns_first, "messages", (AIMessageChunk(content="X-text"), {})),
            (ns_second, "messages", (AIMessageChunk(content=" more-Y"), {})),
        ]
    )
    bridge = DeepAgentIPCStream(graph, config=_make_config(), state={})

    items = await _collect_items(bridge)

    deltas = [item for item in items if item.get("event_type") == "subagent_message_delta"]
    x_text = "".join(d["content"] for d in deltas if d["metadata"]["tool_call_id"] == "call_x")
    y_text = "".join(d["content"] for d in deltas if d["metadata"]["tool_call_id"] == "call_y")
    assert x_text == "X-text"
    assert y_text == "Y-text more-Y"


@pytest.mark.asyncio
async def test_native_reasoning_deltas_stream_without_synthetic_preview():
    reasoning_fragments = [
        "**Calculating pencil costs**\n\nI'm",
        " figuring it out.",
    ]
    graph = _FakeGraph(
        [
            (
                "messages",
                (
                    AIMessageChunk(
                        content=[
                            {
                                "type": "reasoning",
                                "summary": [
                                    {"type": "summary_text", "text": reasoning_fragments[0]}
                                ],
                            }
                        ]
                    ),
                    {"langgraph_node": "respond"},
                ),
            ),
            (
                "messages",
                (
                    AIMessageChunk(
                        content=[
                            {
                                "type": "reasoning",
                                "summary": [
                                    {"type": "summary_text", "text": reasoning_fragments[1]}
                                ],
                            },
                            {"type": "text", "text": "Hi"},
                        ]
                    ),
                    {"langgraph_node": "respond"},
                ),
            ),
            ("updates", {"respond": {"pending_response": "Hi"}}),
        ]
    )
    bridge = DeepAgentIPCStream(graph, config=_make_config(reasoning_enabled=True), state={})

    items = await _collect_items(bridge)

    reasoning_events = [item for item in items if item.get("event_type") == "reasoning_delta"]
    assert "".join(item["content"] for item in reasoning_events) == "".join(reasoning_fragments)
    assert all("synthetic" not in item["metadata"] for item in reasoning_events)
    assert all("preview" not in item["metadata"] for item in reasoning_events)


@pytest.mark.asyncio
async def test_final_reasoning_summary_only_updates_tokens_after_streamed_deltas():
    graph = _FakeGraph(
        [
            (
                "messages",
                (
                    AIMessageChunk(
                        content=[
                            {
                                "type": "reasoning",
                                "summary": [{"type": "summary_text", "text": "First. "}],
                            }
                        ]
                    ),
                    {"langgraph_node": "model"},
                ),
            ),
            (
                "messages",
                (
                    AIMessageChunk(
                        content=[
                            {
                                "type": "reasoning",
                                "summary": [{"type": "summary_text", "text": "Second."}],
                            },
                            {"type": "text", "text": "Done"},
                        ]
                    ),
                    {"langgraph_node": "model"},
                ),
            ),
            (
                "updates",
                {
                    "respond": {
                        "pending_response": "Done",
                        "messages": [
                            AIMessage(
                                content=[
                                    {
                                        "type": "reasoning",
                                        "summary": [
                                            {"type": "summary_text", "text": "First. Second."}
                                        ],
                                    },
                                    {"type": "text", "text": "Done"},
                                ],
                                usage_metadata={
                                    "input_tokens": 1,
                                    "output_tokens": 8,
                                    "total_tokens": 9,
                                    "output_token_details": {"reasoning": 7},
                                },
                            )
                        ],
                    }
                },
            ),
        ]
    )
    bridge = DeepAgentIPCStream(graph, config=_make_config(reasoning_enabled=True), state={})

    items = await _collect_items(bridge)

    reasoning_deltas = [item for item in items if item.get("event_type") == "reasoning_delta"]
    reasoning_summaries = [item for item in items if item.get("event_type") == "reasoning_summary"]

    assert "".join(item["content"] for item in reasoning_deltas) == "First. Second."
    assert reasoning_summaries == [
        {
            "type": "stream_event",
            "event_type": "reasoning_summary",
            "content": "First. Second.",
            "metadata": {"reasoning_tokens": 7, "text_length": 14},
        }
    ]
    assert bridge.last_reasoning_trace == "First. Second."


@pytest.mark.asyncio
async def test_streamed_reasoning_survives_when_final_message_drops_it():
    graph = _FakeGraph(
        [
            (
                "messages",
                (
                    AIMessageChunk(
                        content=[
                            {
                                "type": "reasoning",
                                "summary": [{"type": "summary_text", "text": "Checked inputs. "}],
                            }
                        ]
                    ),
                    {"langgraph_node": "model"},
                ),
            ),
            (
                "messages",
                (
                    AIMessageChunk(
                        content=[
                            {
                                "type": "reasoning",
                                "summary": [{"type": "summary_text", "text": "Computed answer."}],
                            },
                            {"type": "text", "text": "Done"},
                        ]
                    ),
                    {"langgraph_node": "model"},
                ),
            ),
            (
                "updates",
                {
                    "respond": {
                        "pending_response": "Done",
                        "messages": [
                            AIMessage(
                                content=[{"type": "text", "text": "Done"}],
                                usage_metadata={
                                    "input_tokens": 1,
                                    "output_tokens": 8,
                                    "total_tokens": 9,
                                    "output_token_details": {"reasoning": 7},
                                },
                            )
                        ],
                    }
                },
            ),
        ]
    )
    bridge = DeepAgentIPCStream(graph, config=_make_config(reasoning_enabled=True), state={})

    items = await _collect_items(bridge)

    reasoning_summaries = [item for item in items if item.get("event_type") == "reasoning_summary"]

    assert reasoning_summaries == [
        {
            "type": "stream_event",
            "event_type": "reasoning_summary",
            "content": "Checked inputs. Computed answer.",
            "metadata": {"reasoning_tokens": 7, "text_length": 32},
        }
    ]
    assert bridge.last_reasoning_trace == "Checked inputs. Computed answer."
    assert (
        extract_reasoning_trace(bridge.state["messages"][-1]) == "Checked inputs. Computed answer."
    )
