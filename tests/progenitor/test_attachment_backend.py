"""AttachmentDirectoryBackend must scope every read/listing to the current
turn's thread -- regression coverage for the cross-thread attachment leak
(agent-side mount at /attachments/)."""

from __future__ import annotations

import pytest

from app.progenitor import attachments_db
from app.progenitor.agent.attachment_backend import AttachmentDirectoryBackend
from app.progenitor.runtime.thread_context import (
    reset_current_thread_id,
    set_current_thread_id,
)


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
