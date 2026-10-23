"""AttachmentDirectoryBackend must scope every read/listing to the current
turn's thread -- regression coverage for the cross-thread attachment leak
(agent-side mount at /attachments/)."""

from __future__ import annotations

import base64

import pytest

import src.progena.attachments.store as attachments_db
from src.progena.agent.filesystem.attachments import AttachmentDirectoryBackend
from src.progena.sessions.context import reset_current_thread_id, set_current_thread_id


@pytest.fixture
def backend():
    return AttachmentDirectoryBackend("postgresql://example")


class TestReadIsThreadScoped:
    async def test_read_without_active_thread_errors(self, backend):
        result = await backend.aread("/file.txt")
        assert result.error is not None
        assert "no active conversation thread" in result.error.lower()

    async def test_read_passes_current_thread_id_to_the_query(self, backend, monkeypatch):
        captured: dict[str, object] = {}

        async def fake_fetch_by_name(database_url, filename, *, thread_id):
            captured["database_url"] = database_url
            captured["filename"] = filename
            captured["thread_id"] = thread_id
            return {"text_content": "hi", "mime_type": "text/plain", "extraction_error": None}

        monkeypatch.setattr(attachments_db, "fetch_by_name", fake_fetch_by_name)

        token = set_current_thread_id("thread-a")
        try:
            result = await backend.aread("/notes.txt")
        finally:
            reset_current_thread_id(token)

        assert captured == {
            "database_url": "postgresql://example",
            "filename": "notes.txt",
            "thread_id": "thread-a",
        }
        assert result.error is None

    async def test_read_only_sees_its_own_threads_row(self, backend, monkeypatch):
        """fetch_by_name is thread-scoped in the query itself; simulate the
        real behavior (row absent for a non-owning thread) and confirm the
        backend surfaces that as a normal not-found, not a leak."""

        async def fake_fetch_by_name(database_url, filename, *, thread_id):
            return None if thread_id != "owner-thread" else {"text_content": "secret"}

        monkeypatch.setattr(attachments_db, "fetch_by_name", fake_fetch_by_name)

        token = set_current_thread_id("attacker-thread")
        try:
            result = await backend.aread("/secret.txt")
        finally:
            reset_current_thread_id(token)

        assert result.error == "File '/secret.txt' not found"


class TestMultimodalExtensionsReturnRealBase64:
    """Regression: deepagents' built-in read_file tool ALWAYS wraps
    known binary extensions (.pdf, .png, ...) as a multimodal content
    block regardless of the encoding this backend declares -- it decides
    purely from the file extension (see deepagents.middleware.filesystem).
    Handing back plain extracted text for a .pdf used to get silently
    mislabeled "base64" and blow up the Responses API with an
    invalid-base64 error on every read. These extensions must always get
    the real raw bytes, base64-encoded -- never text_content.
    """

    async def test_pdf_read_returns_raw_bytes_base64_encoded_not_extracted_text(
        self, backend, monkeypatch
    ):
        raw_pdf_bytes = b"%PDF-1.4 fake pdf bytes"

        async def fake_fetch_by_name(database_url, filename, *, thread_id):
            return {
                "text_content": "best-effort extracted text, not real base64",
                "data": raw_pdf_bytes,
                "mime_type": "application/pdf",
            }

        monkeypatch.setattr(attachments_db, "fetch_by_name", fake_fetch_by_name)

        token = set_current_thread_id("thread-a")
        try:
            result = await backend.aread("/report.pdf")
        finally:
            reset_current_thread_id(token)

        assert result.error is None
        assert result.file_data["encoding"] == "base64"
        assert base64.b64decode(result.file_data["content"]) == raw_pdf_bytes

    async def test_text_file_read_still_returns_extracted_text(self, backend, monkeypatch):
        async def fake_fetch_by_name(database_url, filename, *, thread_id):
            return {
                "text_content": "plain notes",
                "data": b"plain notes",
                "mime_type": "text/plain",
            }

        monkeypatch.setattr(attachments_db, "fetch_by_name", fake_fetch_by_name)

        token = set_current_thread_id("thread-a")
        try:
            result = await backend.aread("/notes.txt")
        finally:
            reset_current_thread_id(token)

        assert result.error is None
        assert result.file_data["content"] == "plain notes"
        assert result.file_data.get("encoding", "utf-8") == "utf-8"


class TestListingIsThreadScoped:
    async def test_ls_without_active_thread_returns_empty(self, backend):
        result = await backend.als("/")
        assert result.entries == []

    async def test_ls_only_lists_the_current_threads_filenames(self, backend, monkeypatch):
        async def fake_list_filenames(database_url, *, thread_id):
            return ["mine.txt"] if thread_id == "thread-a" else ["theirs.txt"]

        monkeypatch.setattr(attachments_db, "list_filenames", fake_list_filenames)

        token = set_current_thread_id("thread-a")
        try:
            result = await backend.als("/")
        finally:
            reset_current_thread_id(token)

        assert [entry["path"] for entry in result.entries] == ["/mine.txt"]


async def test_bounded_sandbox_import_preserves_original_bytes_and_thread_scope(postgres_url):
    pool = await attachments_db.get_pool(postgres_url)
    raw = b"%PDF-\x00\xff original upload"
    try:
        async with pool.connection() as conn, conn.cursor() as cur:
            for identifier, thread, content, time_offset in [
                ("older", "owner", b"outdated", -1),
                ("mine", "owner", raw, 0),
                ("theirs", "other", b"private other conversation", 1),
            ]:
                await cur.execute(
                    "INSERT INTO attachments "
                    "(id, thread_id, filename, mime_type, size_bytes, checksum, data, "
                    "text_content, created_at) VALUES (%s,%s,'report.pdf','application/pdf',"
                    "%s,'fixture',%s,'extracted text is not the upload', now() + %s * interval '1 second')",
                    (identifier, thread, len(content), content, time_offset),
                )
        mount = AttachmentDirectoryBackend(postgres_url)
        token = set_current_thread_id("owner")
        try:
            assert await mount.adownload_file_bounded("/report.pdf", len(raw)) == raw
            with pytest.raises(ValueError):
                await mount.adownload_file_bounded("/report.pdf", len(raw) - 1)
        finally:
            reset_current_thread_id(token)
        token = set_current_thread_id("unrelated")
        try:
            with pytest.raises(FileNotFoundError):
                await mount.adownload_file_bounded("/report.pdf", 1024)
        finally:
            reset_current_thread_id(token)
    finally:
        await attachments_db.close_all_pools()
