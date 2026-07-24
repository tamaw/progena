"""Attachment blob storage endpoints for chat file attachments.

Files are uploaded directly to the Postgres ``attachments`` blob table
(``attachment_blob_store.py``) -- raw bytes plus best-effort extracted text,
not a JSON document cache. The Progenitor runtime reads the same table
directly (``app/progenitor/agent/attachment_backend.py``, mounted at
``/attachments/``) so the DeepAgent can see and read uploaded files itself,
independent of whatever text gets stuffed into a given prompt.

Upload responds synchronously with the id the caller needs right away to
attach the file to its own next ``POST /api/ui/responses`` call, but the
codebase's canonical event pattern (see docs/ARCHITECTURE_DIAGRAM.md) still
applies: on completion this also broadcasts ``attachment.uploaded`` on the
canonical ``/api/ui/stream`` so a file-browser view (or another tab) in the
frontend hears about it without polling.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response

from .attachment_blob_store import get_attachment_blob_store
from .attachments import AttachmentManager, create_attachment_from_upload
from .ui_routes import broadcast_ui_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/attachments", tags=["attachments"])

# Shared manager: extraction/validation logic over the process-wide blob store.
_attachment_manager = AttachmentManager()


def _metadata_payload(meta) -> dict:  # meta: AttachmentBlobMeta
    return {
        "id": meta.id,
        "name": meta.filename,
        "mime_type": meta.mime_type,
        "size_bytes": meta.size_bytes,
        "thread_id": meta.thread_id,
        "created_at": meta.created_at.isoformat() if meta.created_at else None,
        "has_text": bool(meta.text_content),
        "error": meta.extraction_error,
    }


@router.post("")
async def upload_attachment(file: UploadFile = File(...)) -> dict:
    """Upload one file: persisted as a blob row, returns its id.

    The caller sends the returned ``id`` back in ``POST /api/ui/responses``
    metadata (``{"attachments": [id, ...]}``) to attach it to a message.
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

    meta = get_attachment_blob_store().get_by_checksum(result.checksum)
    if meta is not None:
        broadcast_ui_event("attachment.uploaded", _metadata_payload(meta), scope="global")

    return result.model_dump(mode="json")


@router.get("")
async def list_attachments(
    thread_id: str | None = Query(default=None),
    limit: int = Query(default=200, le=1000),
) -> dict:
    """List attachment metadata (no bytes) for the file-browser view."""
    rows = get_attachment_blob_store().list_metadata(thread_id=thread_id, limit=limit)
    return {"attachments": [_metadata_payload(row) for row in rows]}


@router.get("/{attachment_id}")
async def download_attachment(attachment_id: str) -> Response:
    """Return the raw bytes of one attachment (preview/download)."""
    blob = get_attachment_blob_store().get(attachment_id)
    if blob is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return Response(
        content=blob.data,
        media_type=blob.mime_type,
        headers={"Content-Disposition": f'inline; filename="{blob.filename}"'},
    )


@router.delete("/{attachment_id}", status_code=204)
async def delete_attachment(attachment_id: str) -> Response:
    deleted = get_attachment_blob_store().delete(attachment_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Attachment not found")
    broadcast_ui_event("attachment.deleted", {"id": attachment_id}, scope="global")
    return Response(status_code=204)


def get_attachment_manager() -> AttachmentManager:
    """Expose the shared manager so other backend modules can resolve ids."""
    return _attachment_manager
