"""Behavioral coverage for the main chat's file deletion tool."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

from src.progena.agent.filesystem.mounts import FileOnlyDeleteBackend, ReadOnlyBackend
from src.progena.agent.filesystem.routines import RoutineDirectoryBackend
from src.progena.routines.store import default_routine_graph


class _ToolCallingModel(FakeMessagesListChatModel):
    """Scripted model which accepts the filesystem tools supplied by the graph."""

    def bind_tools(self, tools: Sequence[BaseTool], **kwargs: Any) -> _ToolCallingModel:
        return self


class _RoutineStateManager:
    def __init__(self) -> None:
        self.graphs: dict[str, dict[str, Any]] = {}

    async def get_execution_policy(self, policy_id: str) -> dict[str, str]:
        return {"id": policy_id}

    async def get_routine_graph(self, routine_id: str) -> dict[str, Any] | None:
        return self.graphs.get(routine_id)

    async def save_routine_graph(self, routine_id: str, graph: dict[str, Any]) -> None:
        self.graphs[routine_id] = graph

    async def delete_routine_graph(self, routine_id: str) -> None:
        self.graphs.pop(routine_id, None)

    async def list_routine_graphs(self) -> list[dict[str, Any]]:
        return list(self.graphs.values())


def _tool_call(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _chat_graph(backend: CompositeBackend, responses: list[AIMessage]):
    return create_agent(
        _ToolCallingModel(responses=responses),
        tools=[],
        middleware=[
            FilesystemMiddleware(
                backend=FileOnlyDeleteBackend(backend),
                tools=["read_file", "write_file", "delete"],
            )
        ],
    )


def _tool_messages(result: dict[str, Any]) -> list[ToolMessage]:
    return [message for message in result["messages"] if isinstance(message, ToolMessage)]


async def test_chat_tool_creates_then_deletes_routine_document() -> None:
    state_manager = _RoutineStateManager()
    routine_backend = RoutineDirectoryBackend(state_manager)
    backend = CompositeBackend(default=StateBackend(), routes={"/routines/": routine_backend})
    document = json.dumps(default_routine_graph("daily-brief", name="Daily brief"))
    graph = _chat_graph(
        backend,
        [
            _tool_call(
                "write_file",
                {"file_path": "/routines/daily-brief.json", "content": document},
                "write-routine",
            ),
            _tool_call("delete", {"file_path": "/routines/daily-brief.json"}, "delete-routine"),
            AIMessage(content="complete"),
        ],
    )

    result = await graph.ainvoke({"messages": [HumanMessage(content="manage routine")]})

    assert [message.status for message in _tool_messages(result)] == ["success", "success"]
    assert await state_manager.list_routine_graphs() == []
    assert (await routine_backend.als("/")).entries == []
    assert await state_manager.get_routine_graph("daily-brief") is None


async def test_chat_delete_refuses_read_only_missing_and_directory_paths(tmp_path: Path) -> None:
    readonly_root = tmp_path / "readonly"
    readonly_root.mkdir()
    retained = readonly_root / "retained.txt"
    retained.write_text("keep", encoding="utf-8")
    state_manager = _RoutineStateManager()
    backend = CompositeBackend(
        default=StateBackend(),
        routes={
            "/readonly/": ReadOnlyBackend(FilesystemBackend(readonly_root, virtual_mode=True)),
            "/routines/": RoutineDirectoryBackend(state_manager),
        },
    )
    graph = _chat_graph(
        backend,
        [
            _tool_call("delete", {"file_path": "/readonly/retained.txt"}, "read-only"),
            _tool_call("delete", {"file_path": "/routines/missing.json"}, "missing"),
            _tool_call("delete", {"file_path": "/routines/"}, "directory"),
            AIMessage(content="complete"),
        ],
    )

    result = await graph.ainvoke({"messages": [HumanMessage(content="remove files")]})

    messages = _tool_messages(result)
    assert [message.status for message in messages] == ["error", "error", "error"]
    assert "read-only" in str(messages[0].content).lower()
    assert "not found" in str(messages[1].content).lower()
    assert "directory" in str(messages[2].content).lower()
    assert retained.read_text(encoding="utf-8") == "keep"
