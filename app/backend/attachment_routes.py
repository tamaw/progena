"""Attachment upload endpoint for chat message attachments.

Unlike the ``/api/ui/*`` UI-stream routes, this endpoint has no cross-tab
broadcast concern: the uploaded attachment is only ever needed by the
requesting client to reference (by id) in its own subsequent
``POST /api/ui/responses`` call, so it responds synchronously with the
processed :class:`AttachmentResult` instead of going through the 202
acknowledgement + SSE broadcast pattern used by shared session state.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, UploadFile

from .attachments import AttachmentManager, create_attachment_from_upload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/attachments", tags=["attachments"])

# Shared manager: caches processed attachments (keyed by content checksum and
# by id) in the backend document store so re-uploading identical content or
# looking the attachment back up by id during message send is cheap.
_attachment_manager = AttachmentManager()


@router.post("")
async def upload_attachment(file: UploadFile = File(...)) -> dict:
    """Upload and process one file attachment for use in a chat message.

    Returns the processed :class:`AttachmentResult` (id, extracted content,
    truncation info). The caller sends the returned ``id`` back in
    ``POST /api/ui/responses`` metadata (``{"attachments": [id, ...]}``) to
    attach it to a message.
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    result = await create_attachment_from_upload(
        file_content=content,
        filename=file.filename or "upload",
        content_type=file.content_type or "application/octet-stream",
        manager=_attachment_manager,
    )
    if result.error:
        raise HTTPException(status_code=400, detail=result.error)
    return result.model_dump(mode="json")


def get_attachment_manager() -> AttachmentManager:
    """Expose the shared manager so other backend modules can resolve ids."""
    return _attachment_manager
