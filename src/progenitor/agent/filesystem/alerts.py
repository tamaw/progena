"""Alerts capabilities."""

from __future__ import annotations

import json
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
    file_data_to_string,
    perform_string_replacement,
    slice_read_response,
)

from src.progenitor.agent.filesystem.base import _AsyncOnlyFileBackendMixin, _doc_to_text
from src.progenitor.documents.catalog import check_entry_schema
from src.progenitor.utils import utcnow


class AlertDirectoryBackend(_AsyncOnlyFileBackendMixin):
    """Writable ``/alerts/<id>.json`` files for user-facing alert documents."""

    _ASYNC_ONLY = "AlertDirectoryBackend is async-only; use the async file tools."
    _SERVER_FIELDS = frozenset({"id", "timestamp", "viewed", "viewed_at", "source", "context"})

    def __init__(self, state_manager: Any) -> None:
        self._sm = state_manager

    @staticmethod
    def _alert_id(file_path: str) -> str | None:
        name = file_path.lstrip("/")
        if "/" in name or not name.endswith(".json"):
            return None
        alert_id = name.removesuffix(".json").strip()
        return alert_id or None

    async def _alert(self, file_path: str) -> dict[str, Any] | None:
        alert_id = self._alert_id(file_path)
        if alert_id is None:
            return None
        return next((a for a in await self._sm.list_alerts() if a.get("id") == alert_id), None)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        alert = await self._alert(file_path)
        if alert is None:
            return ReadResult(error=f"File '{file_path}' not found")
        file_data = create_file_data(_doc_to_text(alert))
        sliced = slice_read_response(file_data, offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(
            file_data=FileData(content=sliced, encoding=file_data.get("encoding", "utf-8"))
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        alert_id = self._alert_id(file_path)
        if alert_id is None:
            return WriteResult(error="Alert filenames must be '<id>.json' directly under /alerts/")
        try:
            supplied = json.loads(content)
        except json.JSONDecodeError as exc:
            return WriteResult(error=f"Invalid JSON: {exc}")
        if not isinstance(supplied, dict):
            return WriteResult(error="Alert content must be a JSON object")
        server_fields = self._SERVER_FIELDS.intersection(supplied)
        if server_fields:
            return WriteResult(
                error=f"Alert fields are server-managed: {', '.join(sorted(server_fields))}"
            )
        unknown_fields = set(supplied) - {
            "type",
            "title",
            "message",
            "urgent",
            "requires_response",
        }
        if unknown_fields:
            return WriteResult(error=f"Unknown alert fields: {', '.join(sorted(unknown_fields))}")
        payload = {
            "id": alert_id,
            "type": supplied.get("type", "info"),
            "title": supplied.get("title"),
            "message": supplied.get("message"),
            "timestamp": utcnow().timestamp(),
            "urgent": supplied.get("urgent", False),
            "requires_response": supplied.get("requires_response", False),
            "viewed": False,
            "viewed_at": None,
            "source": "progenitor",
        }
        try:
            check_entry_schema("alert", payload, "alert")
        except ValueError as exc:
            return WriteResult(error=str(exc))
        await self._sm.save_alert(payload)
        return WriteResult(path=file_path)

    async def aedit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        read = await self.aread(file_path, 0, 10_000_000)
        if read.error is not None or read.file_data is None:
            return EditResult(error=read.error or f"File '{file_path}' not found")
        current = file_data_to_string(read.file_data)
        result = perform_string_replacement(current, old_string, new_string, replace_all)
        if isinstance(result, str):
            return EditResult(error=result)
        new_content, occurrences = result
        try:
            edited_payload = json.loads(new_content)
        except json.JSONDecodeError as exc:
            return EditResult(error=f"Error: Invalid JSON: {exc}")
        if isinstance(edited_payload, dict):
            for field in self._SERVER_FIELDS:
                edited_payload.pop(field, None)
            new_content = json.dumps(edited_payload)
        written = await self.awrite(file_path, new_content)
        if written.error is not None:
            return EditResult(error=f"Error: {written.error}")
        return EditResult(path=file_path, occurrences=int(occurrences))

    async def adelete(self, file_path: str) -> WriteResult:
        return WriteResult(error="/alerts/ cannot delete alert history")

    async def als(self, path: str) -> LsResult:
        return LsResult(
            entries=[
                FileInfo(path=f"/{alert['id']}.json", is_dir=False)
                for alert in await self._sm.list_alerts()
                if alert.get("id")
            ]
        )
