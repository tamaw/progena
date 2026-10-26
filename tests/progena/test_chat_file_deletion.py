"""Behavioral coverage for the main chat's file deletion tool."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from deepagents.backends import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

from src.progena.agent.filesystem.content import InstructionDocumentDirectoryBackend
from src.progena.agent.filesystem.mounts import (
    MountAccessCompositeBackend,
    create_mount_backend,
    mount_access,
)
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


def _chat_graph(backend: MountAccessCompositeBackend, responses: list[AIMessage]):
    return create_agent(
        _ToolCallingModel(responses=responses),
        tools=[],
        middleware=[
            FilesystemMiddleware(backend=backend, tools=["read_file", "write_file", "delete"])
        ],
    )


def _tool_messages(result: dict[str, Any]) -> list[ToolMessage]:
    return [message for message in result["messages"] if isinstance(message, ToolMessage)]


async def test_chat_tool_creates_then_deletes_routine_document() -> None:
    state_manager = _RoutineStateManager()
    routine_backend = RoutineDirectoryBackend(state_manager)
    backend = create_mount_backend({"/routines/": routine_backend})
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


async def test_chat_delete_follows_mount_access(tmp_path: Path) -> None:
    roots = {name: tmp_path / name for name in ("self", "memories", "shared")}
    for root in roots.values():
        root.mkdir()
        (root / "keep.txt").write_text("keep", encoding="utf-8")
    (roots["shared"] / "drop.txt").write_text("drop", encoding="utf-8")
    (roots["shared"] / "empty").mkdir()
    (roots["shared"] / "full").mkdir()
    (roots["shared"] / "full" / "nested.txt").write_text("keep", encoding="utf-8")
    backend = create_mount_backend(
        {
            f"/{name}/": FilesystemBackend(root_dir=root, virtual_mode=True)
            for name, root in roots.items()
        }
    )
    calls = [
        ("/self/keep.txt", "read"),
        ("/memories/keep.txt", "read-write"),
        ("/shared/empty/", "directory"),
        ("/shared/full", "directory"),
        ("/shared", "directory"),
        ("/shared/../memories/keep.txt", "traversal"),
        ("/shared/missing.txt", "not found"),
        ("/shared/drop.txt", None),
    ]
    graph = _chat_graph(
        backend,
        [
            *(
                _tool_call("delete", {"file_path": path}, f"call-{i}")
                for i, (path, _) in enumerate(calls)
            ),
            AIMessage(content="complete"),
        ],
    )

    result = await graph.ainvoke({"messages": [HumanMessage(content="remove files")]})

    for message, (path, expected) in zip(_tool_messages(result), calls, strict=True):
        if expected is None:
            assert message.status == "success", path
        else:
            assert message.status == "error", path
            assert expected in str(message.content), (path, message.content)
    assert not (roots["shared"] / "drop.txt").exists()
    assert (roots["shared"] / "empty").is_dir()
    assert (roots["shared"] / "full" / "nested.txt").exists()
    assert all((root / "keep.txt").exists() for root in roots.values())


async def test_scratch_files_keep_state_channel_and_refuse_directory_delete() -> None:
    # The composite must stay recognizable to Deep Agents, or the `files` state
    # channel disappears and every scratch write fails.
    graph = _chat_graph(
        create_mount_backend({}),
        [
            _tool_call("write_file", {"file_path": "/notes/a.txt", "content": "hi"}, "write"),
            _tool_call("read_file", {"file_path": "/notes/a.txt"}, "read"),
            _tool_call("delete", {"file_path": "/notes"}, "delete-dir"),
            _tool_call("delete", {"file_path": "/notes/a.txt"}, "delete-file"),
            AIMessage(content="complete"),
        ],
    )

    result = await graph.ainvoke({"messages": [HumanMessage(content="scratch")]})

    messages = _tool_messages(result)
    assert [message.status for message in messages] == ["success", "success", "error", "success"]
    assert "hi" in str(messages[1].content)
    assert "directory" in str(messages[2].content)
    assert "/notes/a.txt" not in (result.get("files") or {})


async def test_runtime_instructions_cannot_be_deleted() -> None:
    class _NoStore:
        async def get_content_document(self, kind: str, name: str) -> str | None:
            raise AssertionError("runtime instruction deletion must be refused first")

    backend = InstructionDocumentDirectoryBackend(_NoStore(), "instruction")

    result = await backend.adelete("/progena/genesis.md")

    assert result.error is not None and "runtime" in result.error


def test_undeclared_mount_fails_construction() -> None:
    assert mount_access("/skills/market__plugin/") == "read"
    with pytest.raises(ValueError, match="no declared access"):
        create_mount_backend({"/undeclared/": object()})
