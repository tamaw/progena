"""Read-only ``/config/channels/<id>.json`` view of messaging channels."""

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
from deepagents.backends.utils import create_file_data, slice_read_response

from src.progena.agent.filesystem.base import _AsyncOnlyFileBackendMixin, _doc_to_text

_READ_ONLY = "Channels are configured on the Channels page; /config/channels/ is read-only"


class ChannelDirectoryBackend(_AsyncOnlyFileBackendMixin):
    """One ``channel:<id>`` document per file, credentials redacted.

    Shows each channel's type, enabled flag and settings, and for each
    credential only where it comes from and whether it is configured. The
    backend owns writes (type validation and secret encryption).
    """

    _ASYNC_ONLY = "ChannelDirectoryBackend is async-only; use the async file tools."

    def __init__(self, state_manager: Any) -> None:
        self._sm = state_manager

    @staticmethod
    def _channel_id(file_path: str) -> str | None:
        name = file_path.lstrip("/")
        if "/" in name or not name.endswith(".json"):
            return None
        return name.removesuffix(".json") or None

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        channel_id = self._channel_id(file_path)
        channel = await self._sm.get_channel(channel_id) if channel_id else None
        if channel is None:
            return ReadResult(error=f"File '{file_path}' not found")
        file_data = create_file_data(_doc_to_text(channel))
        sliced = slice_read_response(file_data, offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(
            file_data=FileData(content=sliced, encoding=file_data.get("encoding", "utf-8"))
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=_READ_ONLY)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return EditResult(error=_READ_ONLY)

    async def adelete(self, file_path: str) -> WriteResult:
        return WriteResult(error=_READ_ONLY)

    async def als(self, path: str) -> LsResult:
        channels = await self._sm.list_channels()
        return LsResult(
            entries=[
                FileInfo(path=f"/{channel_id}.json", is_dir=False)
                for channel_id in sorted(channels)
            ]
        )
