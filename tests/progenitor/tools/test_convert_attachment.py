"""convert_attachment must resolve/update attachments scoped to the current
turn's thread -- regression coverage for the cross-thread attachment leak."""

from __future__ import annotations

import pytest

import src.progenitor.attachments.store as attachments_db
from src.progenitor.sessions.context import reset_current_thread_id, set_current_thread_id
from src.progenitor.tools.convert_attachment import ConvertAttachmentTool
from src.progenitor.tools.protocol import ToolContext


def _context(attachment: str) -> ToolContext:
    return ToolContext(tool_registry=None, llm_client=None, tool_args={"attachment": attachment})


@pytest.fixture
def tool():
    return ConvertAttachmentTool()


@pytest.fixture(autouse=True)
def database_url_env(monkeypatch):
    monkeypatch.setenv("PROGENITOR_DATABASE_URL", "postgresql://example")


class TestConvertAttachmentIsThreadScoped:
    async def test_fails_without_an_active_thread(self, tool):
        result = await tool.execute(_context("report.pdf"))
        assert not result.success
        assert "no active conversation thread" in (result.error or "").lower()

    async def test_resolves_and_updates_scoped_to_the_current_thread(self, tool, monkeypatch):
        captured: dict[str, object] = {}

        async def fake_fetch_attachment(database_url, identifier, *, thread_id):
            captured["fetch_thread_id"] = thread_id
            captured["identifier"] = identifier
            return {
                "id": "att_1",
                "filename": "report.pdf",
                "mime_type": "application/pdf",
                "data": b"%PDF-fake%",
            }

        async def fake_update_text_content(database_url, attachment_id, text_content, *, thread_id):
            captured["update_thread_id"] = thread_id
            captured["attachment_id"] = attachment_id

        class _FakeResult:
            markdown = "# Report"

        async def _fake_convert(*a, **k):
            return _FakeResult()

        monkeypatch.setattr(attachments_db, "fetch_attachment", fake_fetch_attachment)
        monkeypatch.setattr(attachments_db, "update_text_content", fake_update_text_content)
        monkeypatch.setattr(ConvertAttachmentTool, "_convert", staticmethod(_fake_convert))
        # Bypass the `import markitdown` guard -- this tool degrades to a
        # clean failure when it's not installed, which isn't what this test
        # is about.
        monkeypatch.setitem(
            __import__("sys").modules,
            "markitdown",
            type("m", (), {"MarkItDown": lambda: object()}),
        )
        monkeypatch.setitem(
            __import__("sys").modules,
            "markitdown._exceptions",
            type("m", (), {"MarkItDownException": Exception}),
        )

        token = set_current_thread_id("thread-a")
        try:
            result = await tool.execute(_context("report.pdf"))
        finally:
            reset_current_thread_id(token)

        assert result.success, result.error
        assert captured["fetch_thread_id"] == "thread-a"
        assert captured["update_thread_id"] == "thread-a"
        assert captured["identifier"] == "report.pdf"
        assert captured["attachment_id"] == "att_1"

    async def test_cannot_resolve_another_threads_attachment(self, tool, monkeypatch):
        """fetch_attachment is itself thread-scoped; simulate the real
        behavior (row absent for a non-owning thread) end to end."""

        async def fake_fetch_attachment(database_url, identifier, *, thread_id):
            return None if thread_id != "owner-thread" else {"id": "att_1", "filename": "x"}

        monkeypatch.setattr(attachments_db, "fetch_attachment", fake_fetch_attachment)

        token = set_current_thread_id("attacker-thread")
        try:
            result = await tool.execute(_context("x"))
        finally:
            reset_current_thread_id(token)

        assert not result.success
        assert "no attachment found" in (result.error or "").lower()
