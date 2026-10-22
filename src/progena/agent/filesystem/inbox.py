"""Inbox capabilities."""

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

from src.progena.agent.filesystem.base import _AsyncOnlyFileBackendMixin
from src.progena.inbox.documents import inbox_message_to_markdown, markdown_to_inbox_message


class InboxDirectoryBackend(_AsyncOnlyFileBackendMixin):
    """Async DeepAgent backend exposing the user's inbox as a message directory.

    Mounted at ``/inbox/``: every message is one ``<id>.md`` file (YAML
    frontmatter for the fields, markdown body for the content). Each message is
    its own ``inbox_message:<id>`` document in the shared Postgres store, so
    the agent composes one message per file and a write touches a single row.
    Persistence flows through the StateManager, so the existing LISTEN/NOTIFY
    pipeline pushes ``inbox.snapshot`` to the Inbox UI with no bespoke tool.

    The directory is flat — no index document; the message list is a key-prefix
    scan. ``read``/``read_at`` are user-owned (set by the backend when the user
    opens a message) and should be left alone by the agent.
    """

    _ASYNC_ONLY = "InboxDirectoryBackend is async-only; use the async file tools."

    def __init__(self, state_manager: Any) -> None:
        self._sm = state_manager

    @staticmethod
    def _name(file_path: str) -> str:
        # CompositeBackend strips the /inbox/ route prefix before delegating.
        return file_path.lstrip("/").removeprefix("inbox/")

    @staticmethod
    def _parse_message_name(name: str) -> str | None:
        """Return the message id a filename names, or ``None`` if not a message path."""
        if "/" in name or not name.endswith(".md"):
            return None
        message_id = name[: -len(".md")].strip()
        return message_id or None

    async def _resolve_message(self, name: str) -> tuple[str, dict[str, Any]] | None:
        message_id = self._parse_message_name(name)
        if message_id is None:
            return None
        message = await self._sm.get_inbox_message(message_id)
        if message is None:
            return None
        return message_id, message

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        resolved = await self._resolve_message(self._name(file_path))
        if resolved is None:
            return ReadResult(error=f"File '{file_path}' not found")
        _, message = resolved
        file_data = create_file_data(inbox_message_to_markdown(message))
        sliced = slice_read_response(file_data, offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(
            file_data=FileData(content=sliced, encoding=file_data.get("encoding", "utf-8"))
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        name = self._name(file_path)
        message_id = self._parse_message_name(name)
        if message_id is None:
            return WriteResult(error=f"Unknown inbox file '{file_path}' (expected <id>.md)")
        try:
            message = markdown_to_inbox_message(content, message_id)
        except ValueError as exc:
            return WriteResult(error=f"Invalid inbox message {name}: {exc}")
        await self._sm.save_inbox_message(message_id, message)
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
        resolved = await self._resolve_message(self._name(file_path))
        if resolved is None:
            return WriteResult(error=f"File '{file_path}' not found")
        message_id, _ = resolved
        await self._sm.delete_inbox_message(message_id)
        return WriteResult(path=file_path)

    async def als(self, path: str) -> LsResult:
        messages = await self._sm.list_inbox_messages()
        ordered = sorted(
            (m for m in messages if isinstance(m, dict) and m.get("id")),
            key=lambda m: str(m.get("created_at") or ""),
            reverse=True,
        )
        entries = [FileInfo(path=f"/{message['id']}.md", is_dir=False) for message in ordered]
        return LsResult(entries=entries)
