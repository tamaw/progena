from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from src.progena.agent.filesystem.runs import RunDirectoryBackend
from src.progena.routines.subagent import (
    create_routine_executor,
    resolve_routine_document,
    resume_routine_thread,
)
from src.progena.settings.policies import ExecutionPolicy


class _UsageModel:
    async def ainvoke(self, _messages: Any, *args: Any, **kwargs: Any) -> AIMessage:
        return AIMessage(
            content="Done",
            usage_metadata={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        )


class _DuplicateInterruptSaver(InMemorySaver):
    """Expose an already-completed native child as a duplicate pending write."""

    def __init__(self) -> None:
        super().__init__()
        self.duplicate_thread: str | None = None
        self.duplicate_payload: dict[str, Any] | None = None

    async def aget_tuple(self, config: dict[str, Any]) -> Any:
        tip = await super().aget_tuple(config)
        thread_id = config.get("configurable", {}).get("thread_id")
        if tip is None or thread_id != self.duplicate_thread or self.duplicate_payload is None:
            return tip
        return SimpleNamespace(
            checkpoint=tip.checkpoint,
            pending_writes=(
                *(tip.pending_writes or ()),
                (
                    "duplicate-child",
                    "__interrupt__",
                    [SimpleNamespace(value=self.duplicate_payload)],
                ),
            ),
        )


class _RevisionReader:
    async def fetch(self, _routine_id: str, _revision_id: str) -> dict[str, Any] | None:
        return None

    async def fetch_latest(self, _routine_id: str) -> tuple[str, dict[str, Any]] | None:
        return None

    async def fetch_revision_id_for_content(
        self, _routine_id: str, _graph: dict[str, Any]
    ) -> str | None:
        return None

    async def fetch_policy_revision_id(self, _policy_id: str, document: dict[str, Any]) -> str:
        return str(document.get("id") or "default")


class _StateManager:
    def __init__(self, graphs: dict[str, dict[str, Any]]) -> None:
        self.graphs = graphs
        self.events: list[dict[str, Any]] = []

    async def get_routine_graph(self, routine_id: str) -> dict[str, Any] | None:
        return self.graphs.get(routine_id)

    async def list_routine_graphs(self) -> list[dict[str, Any]]:
        return list(self.graphs.values())

    async def get_execution_policy(self, policy_id: str) -> dict[str, Any]:
        return ExecutionPolicy(id=policy_id, name="Default").model_dump(mode="json")

    async def get_model_profiles_config(self) -> dict[str, Any]:
        return {}

    async def save_routine_run_event(self, payload: dict[str, Any]) -> None:
        self.events.append(dict(payload))

    async def list_routine_run_events(
        self, request_id: str | None = None, *, thread_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        return [
            event
            for event in self.events
            if (request_id is None or event.get("request_id") == request_id)
            and (thread_ids is None or event.get("thread_id") in thread_ids)
        ]

    async def get_routine_run_started(self, request_id: str) -> dict[str, Any] | None:
        return next(
            (
                event
                for event in self.events
                if event.get("request_id") == request_id and event.get("status") == "started"
            ),
            None,
        )

    async def list_routine_run_event_revisions(
        self, *, thread_ids: list[str]
    ) -> list[dict[str, Any]]:
        return [
            {
                key: event[key]
                for key in ("request_id", "status", "timestamp", "thread_id", "runtime_instance")
                if key in event
            }
            for event in self.events
            if event.get("thread_id") in thread_ids
        ]

    async def get_preferences_config(self) -> dict[str, Any]:
        return {"timezone": "UTC"}

    async def list_routine_titles(self) -> dict[str, str]:
        return {
            routine_id: str(graph.get("name") or routine_id)
            for routine_id, graph in self.graphs.items()
        }

    async def list_routine_run_summaries(self) -> list[dict[str, Any]]:
        return [
            {
                key: event[key]
                for key in (
                    "request_id",
                    "status",
                    "timestamp",
                    "agent_id",
                    "routine_id",
                    "thread_id",
                    "parent",
                    "session_id",
                )
                if key in event
            }
            for event in self.events
        ]


async def _runner(
    state_manager: _StateManager,
    reader: _RevisionReader,
    saver: InMemorySaver,
    model: _UsageModel,
    routine_id_or_name: str,
    state: dict[str, Any],
    runtime: Any = None,
) -> dict[str, Any]:
    resolved = await resolve_routine_document(state_manager, routine_id_or_name)
    assert resolved is not None
    routine_id, _graph = resolved
    executor = create_routine_executor(
        routine_id=routine_id,
        model=model,
        state_manager=state_manager,
        revision_reader=reader,
        agent_registry={},
        routine_registry={},
        routine_runner=lambda name, child_state, child_runtime=None: _runner(
            state_manager, reader, saver, model, name, child_state, child_runtime
        ),
        checkpointer=saver,
        context=dict(getattr(runtime, "context", None) or {}),
    )
    return await executor.ainvoke(state)


def _graph(
    routine_id: str, nodes: list[dict[str, Any]], edges: list[dict[str, str]]
) -> dict[str, Any]:
    return {
        "id": routine_id,
        "name": routine_id.replace("-", " ").title(),
        "description": "test",
        "version": "2.0.0",
        "entry": nodes[0]["id"],
        "nodes": nodes,
        "edges": edges,
    }


def _routine_node(node_id: str, child_id: str) -> dict[str, Any]:
    return {
        "id": node_id,
        "kind": "routine",
        "output_key": node_id,
        "config": {"routine_name": child_id, "input_map": {}, "output_map": {}},
    }


async def _read_all_routine_files(mount: RunDirectoryBackend) -> list[str]:
    listing = await mount.als("/runs/routines")
    return [entry["path"] for entry in listing.entries or []]


async def test_paused_nested_child_is_inlined_from_native_interrupt_and_not_listed() -> None:
    child = _graph(
        "child",
        [
            {
                "id": "evaluate",
                "kind": "eval",
                "label": "Evaluate 42",
                "output_key": "value",
                "config": {"expression": "42"},
            },
            {
                "id": "approval",
                "kind": "human",
                "label": "Approve child",
                "output_key": "approval",
                "config": {"message": "Approve child work?", "allowed_responses": ["approve"]},
            },
            {
                "id": "reply",
                "kind": "prompt",
                "output_key": "reply",
                "config": {"prompt_template": "reply"},
            },
        ],
        [
            {"from": "evaluate", "to": "approval", "level": "normal"},
            {"from": "approval", "to": "reply", "level": "normal"},
        ],
    )
    parent = _graph("parent", [_routine_node("call", "child")], [])
    state_manager = _StateManager({"parent": parent, "child": child})
    reader = _RevisionReader()
    saver = _DuplicateInterruptSaver()
    model = _UsageModel()

    paused = await _runner(state_manager, reader, saver, model, "parent", {})
    parent_thread = paused["_routine_thread_id"]
    parent_started = next(
        event
        for event in state_manager.events
        if event["agent_id"] == "parent" and event["status"] == "started"
    )
    parent_checkpoint = await saver.aget_tuple({"configurable": {"thread_id": parent_thread}})
    assert parent_checkpoint is not None
    assert "call" not in parent_checkpoint.checkpoint["channel_values"].get("run_meta", {})
    native_payload = next(
        item.value
        for _task_id, channel, items in parent_checkpoint.pending_writes
        if channel == "__interrupt__"
        for item in items
        if isinstance(getattr(item, "value", None), dict) and item.value.get("child_invocation")
    )
    child_started = next(
        event
        for event in state_manager.events
        if event["agent_id"] == "child" and event["status"] == "started"
    )
    assert native_payload["node"] == "call"
    assert native_payload["child_invocation"]["agent_id"] == "child"
    assert native_payload["child_invocation"]["request_id"] == child_started["request_id"]
    assert native_payload["child_invocation"]["thread_id"] == child_started["thread_id"]
    assert native_payload["child_invocation"]["session_id"] == child_started["session_id"]

    mount = RunDirectoryBackend(state_manager, saver, reader)
    paused_paths = await _read_all_routine_files(mount)
    assert len(paused_paths) == 1
    assert "child" not in paused_paths[0]
    paused_read = await mount.aread(paused_paths[0], 0, 100_000)
    assert paused_read.error is None and paused_read.file_data is not None
    paused_text = paused_read.file_data["content"]
    assert paused_text.count("→ 42") == 1
    assert "Approve child work? [approve]" in paused_text
    assert "status: paused" in paused_text

    resumed = await resume_routine_thread(
        thread_id=parent_thread,
        answer="approve",
        model=model,
        state_manager=state_manager,
        revision_reader=reader,
        agent_registry={},
        routine_registry={},
        routine_runner=lambda name, child_state, child_runtime=None: _runner(
            state_manager, reader, saver, model, name, child_state, child_runtime
        ),
        checkpointer=saver,
    )
    assert resumed["status"] == "completed"
    completed_checkpoint = await saver.aget_tuple({"configurable": {"thread_id": parent_thread}})
    assert completed_checkpoint is not None
    completed_invocation = completed_checkpoint.checkpoint["channel_values"]["run_meta"]["call"][
        -1
    ]["routine"]
    saver.duplicate_thread = parent_thread
    saver.duplicate_payload = {**native_payload, "child_invocation": completed_invocation}

    resumed_paths = await _read_all_routine_files(mount)
    assert len(resumed_paths) == 2
    assert all("child" not in path for path in resumed_paths)
    rendered = []
    for path in resumed_paths:
        result = await mount.aread(path, 0, 100_000)
        assert result.error is None and result.file_data is not None
        rendered.append(result.file_data["content"])
    resumed_text = next(text for text in rendered if "status: completed" in text)
    front = yaml.safe_load(resumed_text.split("---\n")[1])
    assert "→ 42" not in resumed_text
    assert resumed_text.count("<@reply> Done") == 1
    assert front["tokens"] == {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
    assert parent_started["request_id"] in paused_text


async def test_pending_children_stop_at_render_depth_and_native_cycles_fail() -> None:
    graphs: dict[str, dict[str, Any]] = {}
    for index in range(4):
        routine_id = f"parent-{index}"
        graphs[routine_id] = _graph(
            routine_id,
            [_routine_node("call", f"parent-{index + 1}")],
            [],
        )
    graphs["parent-4"] = _graph(
        "parent-4",
        [
            {
                "id": "approval",
                "kind": "human",
                "output_key": "approval",
                "config": {"message": "Deep approval", "allowed_responses": ["approve"]},
            }
        ],
        [],
    )
    graphs["cycle"] = _graph("cycle", [_routine_node("call", "cycle")], [])
    state_manager = _StateManager(graphs)
    reader = _RevisionReader()
    saver = InMemorySaver()
    model = _UsageModel()

    await _runner(state_manager, reader, saver, model, "parent-0", {})
    mount = RunDirectoryBackend(state_manager, saver, reader)
    path = (await _read_all_routine_files(mount))[0]
    result = await mount.aread(path, 0, 100_000)
    assert result.error is None and result.file_data is not None
    assert result.file_data["content"].count("[routine] has joined") == 3

    with pytest.raises(ValueError, match="nested routine cycle detected"):
        await _runner(state_manager, reader, saver, model, "cycle", {})
