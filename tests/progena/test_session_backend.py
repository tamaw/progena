"""The /conversations/ mount: listing, reading, searching, and editing session documents."""

from __future__ import annotations

import re
from typing import Any

import pytest
from langgraph.store.memory import InMemoryStore

from src.progena.agent.filesystem.mounts import create_progena_backend
from src.progena.agent.filesystem.sessions import SessionDirectoryBackend
from src.progena.documents.kinds import SESSION_KEY_PREFIX
from src.progena.persistence.store import LangGraphRuntimeStore
from src.progena.state.manager import StateManager


class _Sessions:
    def __init__(self) -> None:
        self.store = InMemoryStore()
        self.state_manager = StateManager(LangGraphRuntimeStore(self.store))
        self.backend = SessionDirectoryBackend(self.state_manager)

    async def preferences(self, display_name: str, timezone: str = "UTC") -> None:
        await self.state_manager.save_preferences_config(
            {"display_name": display_name, "timezone": timezone}
        )

    async def session(
        self,
        thread_id: str,
        *,
        title: str = "New chat",
        first_message: str = "hello",
        archived: bool = False,
        content: str = "<user> hello\n<@progena> hi\n",
        created_at: str = "2026-10-12T01:00:00+00:00",
        last_activity: str = "2026-10-12T01:05:00+00:00",
    ) -> None:
        payload = {
            "id": thread_id,
            "thread_id": thread_id,
            "source": "ui",
            "title": title,
            "first_message": first_message,
            "pinned": False,
            "archived": archived,
            "created_at": created_at,
            "last_activity": last_activity,
            "content": content,
        }
        await self.store.aput(
            ("progena", "state"), f"{SESSION_KEY_PREFIX}{thread_id}", {"payload": payload}
        )

    async def names(self) -> list[str]:
        listing = await self.backend.als("/")
        return [entry["path"] for entry in listing.entries or []]

    async def text(self, path: str) -> str:
        result = await self.backend.aread(path, 0, 10_000)
        assert result.error is None, result.error
        assert result.file_data is not None
        return result.file_data["content"]


@pytest.fixture
async def sessions() -> _Sessions:
    s = _Sessions()
    await s.preferences("Alex")
    return s


async def test_only_non_archived_sessions_get_a_file(sessions: _Sessions) -> None:
    await sessions.session("aaaaaaaa-live", title="Weekend plans")
    await sessions.session("bbbbbbbb-archived", archived=True)
    await sessions.session("cccccccc-empty", content="")

    names = await sessions.names()

    assert {name.rsplit("_", 1)[-1] for name in names} == {"aaaaaaaa.md", "cccccccc.md"}


async def test_listing_sorts_offset_session_timestamps_by_instant(sessions: _Sessions) -> None:
    await sessions.session(
        "aaaaaaaa-earlier",
        title="Earlier",
        last_activity="2026-10-12T01:30:00+02:00",
    )
    await sessions.session(
        "bbbbbbbb-later",
        title="Later",
        last_activity="2026-10-11T23:45:00Z",
    )

    assert await sessions.names() == [
        "/2026-10-12_later_bbbbbbbb.md",
        "/2026-10-12_earlier_aaaaaaaa.md",
    ]


async def test_legacy_naive_created_at_is_utc_before_filename_timezone_conversion(
    sessions: _Sessions,
) -> None:
    await sessions.preferences("Alex", timezone="America/Los_Angeles")
    await sessions.session("aaaaaaaa-legacy", created_at="2026-10-12T01:00:00")

    assert await sessions.names() == ["/2026-10-11_hello_aaaaaaaa.md"]
    [path] = await sessions.names()
    assert "created_at: '2026-10-12T01:00:00+00:00'" in await sessions.text(path)
    renamed = (await sessions.text(path)).replace("New chat", "Renamed")
    result = await sessions.backend.awrite(path, renamed)
    assert result.error is None


async def test_archiving_removes_the_file_and_unarchiving_restores_it(sessions: _Sessions) -> None:
    await sessions.session("aaaaaaaa-live")
    [name] = await sessions.names()
    assert "hello" in await sessions.text(name)

    await sessions.session("aaaaaaaa-live", archived=True)
    assert await sessions.names() == []
    assert (await sessions.backend.aread(name)).error is not None

    await sessions.session("aaaaaaaa-live", archived=False)
    assert await sessions.names() == [name]


async def test_read_includes_content(sessions: _Sessions) -> None:
    await sessions.session("aaaaaaaa-live", title="Weekend plans")
    [name] = await sessions.names()
    text = await sessions.text(name)
    assert "title: Weekend plans" in text
    assert "display_title: Weekend plans" in text
    assert "<user> hello" in text
    assert "<@progena> hi" in text


async def test_grep_searches_over_files(sessions: _Sessions) -> None:
    await sessions.session("aaaaaaaa-live", title="Weekend plans")
    [name] = await sessions.names()
    # Called directly on the backend (path is relative to mount point)
    found = await sessions.backend.agrep("<@progena>", "/")
    assert [match["path"] for match in found.matches] == [name]


async def test_write_changes_title(sessions: _Sessions) -> None:
    await sessions.session("aaaaaaaa-live")
    [name] = await sessions.names()
    current = await sessions.text(name)
    new_text = current.replace("title: New chat", "title: Renamed chat")
    result = await sessions.backend.awrite(name, new_text)
    assert result.error is None, result.error

    session = await sessions.state_manager._runtime_store.peek_document(
        f"{SESSION_KEY_PREFIX}aaaaaaaa-live"
    )
    assert isinstance(session, dict)
    assert session["title"] == "Renamed chat"


async def test_write_rejects_body_change(sessions: _Sessions) -> None:
    await sessions.session("aaaaaaaa-live")
    [name] = await sessions.names()
    current = await sessions.text(name)
    new_text = current.replace("<@progena> hi", "<@progena> bye")
    result = await sessions.backend.awrite(name, new_text)
    assert result.error is not None
    assert "body" in result.error.lower() or "front matter" in result.error.lower()


async def test_write_rejects_non_writable_field_change(sessions: _Sessions) -> None:
    await sessions.session("aaaaaaaa-live")
    [name] = await sessions.names()
    current = await sessions.text(name)
    new_text = current.replace("source: ui", "source: api")
    result = await sessions.backend.awrite(name, new_text)
    assert result.error is not None
    assert "source" in result.error


async def test_edit_changes_title(sessions: _Sessions) -> None:
    await sessions.session("aaaaaaaa-live")
    [name] = await sessions.names()
    result = await sessions.backend.aedit(name, "title: New chat", "title: Edited chat")
    assert result.error is None, result.error

    session = await sessions.state_manager._runtime_store.peek_document(
        f"{SESSION_KEY_PREFIX}aaaaaaaa-live"
    )
    assert isinstance(session, dict)
    assert session["title"] == "Edited chat"


async def test_write_rejects_a_value_the_schema_forbids(sessions: _Sessions) -> None:
    await sessions.session("aaaaaaaa-live")
    [name] = await sessions.names()
    current = await sessions.text(name)
    result = await sessions.backend.awrite(name, current.replace("title: New chat", "title: 123"))
    assert result.error is not None and "123" in result.error

    session = await sessions.state_manager._runtime_store.peek_document(
        f"{SESSION_KEY_PREFIX}aaaaaaaa-live"
    )
    assert isinstance(session, dict) and session["title"] == "New chat"


async def test_edit_of_an_ambiguous_string_needs_replace_all(sessions: _Sessions) -> None:
    await sessions.session("aaaaaaaa-live")
    [name] = await sessions.names()

    # pinned: false and archived: false both match.
    ambiguous = await sessions.backend.aedit(name, ": false", ": true")
    assert ambiguous.error is not None and "2 times" in ambiguous.error

    result = await sessions.backend.aedit(name, "pinned: false", "pinned: true")
    assert result.error is None, result.error
    session = await sessions.state_manager._runtime_store.peek_document(
        f"{SESSION_KEY_PREFIX}aaaaaaaa-live"
    )
    assert isinstance(session, dict) and session["pinned"] is True
    assert session["archived"] is False


async def test_delete_is_not_allowed(sessions: _Sessions) -> None:
    await sessions.session("aaaaaaaa-live")
    [name] = await sessions.names()
    result = await sessions.backend.adelete(name)
    assert result.error is not None


async def test_mount_serves_sessions_through_the_agent_filesystem(
    sessions: _Sessions, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr("src.progena.agent.filesystem.mounts.StateBackend", lambda: None)
    filesystem = create_progena_backend(project_root=tmp_path, state_manager=sessions.state_manager)
    await sessions.session("aaaaaaaa-live", title="Weekend plans")

    listing = await filesystem.als("/conversations/")
    [path] = [entry["path"] for entry in listing.entries or []]
    read = await filesystem.aread(path, 0, 100)
    found = await filesystem.agrep("<@progena>", "/conversations/", "*_weekend-plans_*")
    globbed = await filesystem.aglob("*.md", "/conversations/")
    written = await filesystem.awrite(path, "x")

    assert re.fullmatch(r"/conversations/\d{4}-\d{2}-\d{2}_weekend-plans_aaaaaaaa\.md", path)
    assert read.file_data is not None and "<user> hello" in read.file_data["content"]
    assert [match["path"] for match in found.matches] == [path]
    assert [match["path"] for match in globbed.matches or []] == [path]
    assert written.error is not None
