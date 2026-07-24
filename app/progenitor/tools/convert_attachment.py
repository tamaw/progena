"""Convert an uploaded chat attachment to markdown on demand, via markitdown.

Attachments upload with best-effort text extraction already applied (see
``app/backend/attachments.py``), so ``/attachments/<file>`` is usually
readable directly with the file tools. This tool exists for the cases that
extraction misses or does poorly -- PDFs, Word docs, spreadsheets,
presentations -- by running Microsoft's markitdown converter against the
attachment's *raw bytes* (fetched straight from the same Postgres blob
table the backend writes to, via ``app.progenitor.attachments_db``).

The converted markdown is also written back onto the attachment's
``text_content`` column, so a later plain ``/attachments/<file>`` read (or
another agent turn) sees the improved conversion for free instead of
re-running it.
"""

from __future__ import annotations

import io
import logging
import os

from pydantic import BaseModel, Field

from .. import attachments_db
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)

# Bounds one tool call's returned payload, not the file's readable size --
# the full markdown always persists to text_content (see update_text_content
# below), so a caller that needs more just pages through /attachments/<file>
# with offset/limit like any other virtual-fs file. Matches shell.py's
# _MAX_OUTPUT_CHARS precedent for "don't blow up one tool result."
MAX_MARKDOWN_CHARS = 10_000


def _database_url() -> str:
    return (os.getenv("PROGENITOR_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()


class ConvertAttachmentArgs(BaseModel):
    attachment: str = Field(
        description=(
            "The attachment to convert: either its filename as shown under /attachments/ "
            "(e.g. 'report.pdf'), or its id (e.g. 'att_1a2b3c4d')."
        )
    )


class ConvertAttachmentTool(BaseSystemTool):
    """Convert an uploaded attachment (PDF, DOCX, PPTX, XLSX, ...) to markdown."""

    @property
    def name(self) -> str:
        return "convert_attachment"

    @property
    def description(self) -> str:
        return (
            "Convert an uploaded chat attachment to markdown so it can be read as text. "
            "Use this when a file under /attachments/ can't be read directly (binary "
            "formats like PDF, DOCX, PPTX, XLSX) or when its extracted text looks wrong "
            "or truncated. Returns the converted markdown; also updates the attachment "
            "so future reads of /attachments/<file> see the same conversion."
        )

    @property
    def args_model(self) -> type[BaseModel]:
        return ConvertAttachmentArgs

    async def execute(self, context: ToolContext) -> ToolResult:
        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        identifier = str(args.get("attachment") or "").strip()
        if not identifier:
            return ToolResult.fail("Missing 'attachment' (filename or id under /attachments/).")

        database_url = _database_url()
        if not database_url:
            return ToolResult.fail("PROGENITOR_DATABASE_URL is not configured.")

        row = await attachments_db.fetch_attachment(database_url, identifier)
        if row is None:
            return ToolResult.fail(f"No attachment found matching '{identifier}'.")

        try:
            from markitdown import MarkItDown
            from markitdown._exceptions import MarkItDownException
        except ImportError:
            return ToolResult.fail(
                "markitdown is not installed; add it to the environment to use this tool."
            )

        filename = str(row["filename"])
        data = bytes(row["data"])
        extension = os.path.splitext(filename)[1] or None

        try:
            converter = MarkItDown()
            result = await self._convert(converter, data, extension)
        except MarkItDownException as exc:
            return ToolResult.fail(f"Could not convert '{filename}': {exc}")
        except Exception as exc:  # pragma: no cover - defensive against converter internals
            logger.error("convert_attachment failed for %s: %s", filename, exc, exc_info=True)
            return ToolResult.fail(f"Could not convert '{filename}': {exc}")

        markdown = result.markdown or ""
        truncated = len(markdown) > MAX_MARKDOWN_CHARS
        returned_markdown = markdown[:MAX_MARKDOWN_CHARS] if truncated else markdown

        await attachments_db.update_text_content(database_url, str(row["id"]), markdown)

        response: dict[str, object] = {
            "attachment_id": row["id"],
            "filename": filename,
            "mime_type": row["mime_type"],
            "markdown": returned_markdown,
            "truncated": truncated,
            "total_chars": len(markdown),
        }
        if truncated:
            response["note"] = (
                f"Showing the first {MAX_MARKDOWN_CHARS} of {len(markdown)} characters. "
                f"Read the rest with the file tools at /attachments/{filename} "
                f"(offset={MAX_MARKDOWN_CHARS})."
            )
        return ToolResult.ok(response)

    @staticmethod
    async def _convert(converter, data: bytes, extension: str | None):
        """Run markitdown's (sync) conversion off the event loop."""
        import asyncio

        return await asyncio.to_thread(
            converter.convert_stream, io.BytesIO(data), file_extension=extension
        )
