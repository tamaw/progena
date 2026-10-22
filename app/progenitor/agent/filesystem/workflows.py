"""Workflows capabilities."""

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

from app.progenitor.agent.filesystem.base import _AsyncOnlyFileBackendMixin, _doc_to_text
from app.progenitor.instructions.templates import instruction_relative_path
from app.progenitor.workflows.documents import workflow_file_name, workflow_id_from_path
from app.progenitor.workflows.validation import validate_workflow_definition_document


class WorkflowDirectoryBackend(_AsyncOnlyFileBackendMixin):
    """Async backend exposing workflow definitions as ``/workflows/*.json``."""

    _ASYNC_ONLY = "WorkflowDirectoryBackend is async-only; use the async file tools."

    def __init__(self, state_manager: Any) -> None:
        self._sm = state_manager

    @staticmethod
    def _name(file_path: str) -> str:
        return file_path.lstrip("/").removeprefix("workflows/")

    @staticmethod
    def _parse_workflow_name(name: str) -> str | None:
        if "/" in name or not name.endswith(".json"):
            return None
        try:
            return workflow_id_from_path(name)
        except ValueError:
            return None

    async def _resolve_workflow(self, name: str) -> tuple[str, dict[str, Any]] | None:
        workflow_id = self._parse_workflow_name(name)
        if workflow_id is None:
            return None
        workflow = await self._sm.get_workflow_definition(workflow_id)
        if workflow is None:
            return None
        return workflow_id, workflow

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        resolved = await self._resolve_workflow(self._name(file_path))
        if resolved is None:
            return ReadResult(error=f"File '{file_path}' not found")
        _, workflow = resolved
        file_data = create_file_data(_doc_to_text(workflow))
        sliced = slice_read_response(file_data, offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(
            file_data=FileData(content=sliced, encoding=file_data.get("encoding", "utf-8"))
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        name = self._name(file_path)
        workflow_id = self._parse_workflow_name(name)
        if workflow_id is None:
            return WriteResult(error=f"Unknown workflow file '{file_path}' (expected <id>.json)")
        try:
            # Validation is synchronous; snapshot the instructions (by path relative to
            # /instructions/) and routine graphs the workflow may reference.
            instructions = dict(await self._sm.list_content_documents("instruction"))
            routines = {
                str(graph["id"]): graph
                for graph in await self._sm.list_routine_graphs()
                if graph.get("id")
            }

            def _instruction_loader(path: str) -> str:
                text = instructions.get(instruction_relative_path(path))
                if text is None:
                    raise ValueError(f"instruction '{path}' not found")
                return text

            workflow = validate_workflow_definition_document(
                content,
                await self._sm.list_document_types(),
                instruction_loader=_instruction_loader,
                routine_loader=routines.get,
            )
        except ValueError as exc:
            return WriteResult(error=f"Invalid workflow {name}: {exc}")
        if workflow.get("id") != workflow_id:
            return WriteResult(error=f"Workflow id must match filename '{workflow_id}'")
        await self._sm.save_workflow_definition(workflow_id, workflow)
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
        resolved = await self._resolve_workflow(self._name(file_path))
        if resolved is None:
            return WriteResult(error=f"File '{file_path}' not found")
        workflow_id, _ = resolved
        await self._sm.delete_workflow_definition(workflow_id)
        return WriteResult(path=file_path)

    async def als(self, path: str) -> LsResult:
        workflows = await self._sm.list_workflow_definitions()
        ordered = sorted(
            (
                workflow
                for workflow in workflows
                if isinstance(workflow, dict) and workflow.get("id")
            ),
            key=lambda workflow: (
                str(workflow.get("updated_at") or ""),
                str(workflow.get("created_at") or ""),
                str(workflow.get("id") or ""),
            ),
            reverse=True,
        )
        entries = [
            FileInfo(path=f"/{workflow_file_name(str(workflow['id']))}", is_dir=False)
            for workflow in ordered
        ]
        return LsResult(entries=entries)
