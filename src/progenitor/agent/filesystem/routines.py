"""Routines capabilities."""

from __future__ import annotations

from typing import Any

from deepagents.backends.protocol import (
    EditResult,
    FileData,
    FileInfo,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import (
    create_file_data,
    perform_string_replacement,
    slice_read_response,
)

from src.progenitor.agent.filesystem.base import _AsyncOnlyFileBackendMixin, _doc_to_text
from src.progenitor.routines.store import (
    parse_routine_graph_document,
    routine_file_name,
    routine_id_from_path,
)


class RoutineDirectoryBackend(_AsyncOnlyFileBackendMixin):
    """Async backend exposing routine graphs as ``/routines/*.json``.

    Mirrors ``WorkflowDirectoryBackend`` exactly: one JSON document per file,
    validated against ``graph.routine.schema.v1.json`` on write. The document
    ``id`` is the owning agent's id, so this gives the agent the same
    read/write/edit experience over routine graphs that it already has over
    workflow definitions.
    """

    _ASYNC_ONLY = "RoutineDirectoryBackend is async-only; use the async file tools."

    def __init__(self, state_manager: Any) -> None:
        self._sm = state_manager

    @staticmethod
    def _name(file_path: str) -> str:
        return file_path.lstrip("/").removeprefix("routines/")

    @staticmethod
    def _parse_routine_name(name: str) -> str | None:
        if "/" in name or not name.endswith(".json"):
            return None
        try:
            return routine_id_from_path(name)
        except ValueError:
            return None

    async def _resolve_routine(self, name: str) -> tuple[str, dict[str, Any]] | None:
        agent_id = self._parse_routine_name(name)
        if agent_id is None:
            return None
        graph = await self._sm.get_routine_graph(agent_id)
        if graph is None:
            return None
        return agent_id, graph

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        resolved = await self._resolve_routine(self._name(file_path))
        if resolved is None:
            return ReadResult(error=f"File '{file_path}' not found")
        _, graph = resolved
        file_data = create_file_data(_doc_to_text(graph))
        sliced = slice_read_response(file_data, offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(
            file_data=FileData(content=sliced, encoding=file_data.get("encoding", "utf-8"))
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        name = self._name(file_path)
        agent_id = self._parse_routine_name(name)
        if agent_id is None:
            return WriteResult(error=f"Unknown routine file '{file_path}' (expected <id>.json)")
        try:
            graph = parse_routine_graph_document(content)
        except ValueError as exc:
            return WriteResult(error=f"Invalid routine {name}: {exc}")
        if graph.get("id") != agent_id:
            return WriteResult(error=f"Routine id must match filename '{agent_id}'")
        if await self._sm.get_execution_policy(graph["policy_id"]) is None:
            return WriteResult(error=f"Unknown execution policy '{graph['policy_id']}'")
        await self._sm.save_routine_graph(agent_id, graph)
        return WriteResult(path=file_path)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        read = await self.aread(file_path, 0, 10_000_000)
        if read.error is not None:
            return EditResult(error=f"Error: {read.error}")
        current = read.file_data["content"] if read.file_data else ""
        result = perform_string_replacement(current, old_string, new_string, replace_all)
        if isinstance(result, str):
            return EditResult(error=result)
        new_content, occurrences = result
        written = await self.awrite(file_path, new_content)
        if written.error is not None:
            return EditResult(error=f"Error: {written.error}")
        return EditResult(path=file_path, occurrences=int(occurrences))

    async def adelete(self, file_path: str) -> WriteResult:
        resolved = await self._resolve_routine(self._name(file_path))
        if resolved is None:
            return WriteResult(error=f"File '{file_path}' not found")
        agent_id, _ = resolved
        await self._sm.delete_routine_graph(agent_id)
        return WriteResult(path=file_path)

    async def als(self, path: str) -> LsResult:
        graphs = await self._sm.list_routine_graphs()
        ordered = sorted(
            (graph for graph in graphs if isinstance(graph, dict) and graph.get("id")),
            key=lambda graph: (str(graph.get("name") or ""), str(graph.get("id") or "")),
        )
        entries = [
            FileInfo(path=f"/{routine_file_name(str(graph['id']))}", is_dir=False)
            for graph in ordered
        ]
        return LsResult(entries=entries)
