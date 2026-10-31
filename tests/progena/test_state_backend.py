"""Tests for the DeepAgent state-document backend (state-as-files)."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from src.progena.agent.filesystem.base import StateDocumentBackend
from src.progena.agent.filesystem.config import (
    create_health_state_file,
    create_knowledge_state_file,
    create_mcp_state_file,
    create_mcp_tools_state_file,
    create_model_profiles_state_file,
    create_registry_state_file,
)
from src.progena.agent.filesystem.inbox import InboxDirectoryBackend
from src.progena.agent.filesystem.routines import RoutineDirectoryBackend
from src.progena.agent.filesystem.schedules import create_schedule_state_file
from src.progena.agent.filesystem.tasks import KanbanTaskDirectoryBackend
from src.progena.agent.filesystem.workflows import WorkflowDirectoryBackend
from src.progena.inbox.documents import inbox_message_to_markdown, markdown_to_inbox_message
from src.progena.mcp.validation import validate_mcp_document
from src.progena.registry.validation import validate_registry_document
from src.progena.routines.store import default_routine_graph
from src.progena.schedules.models import ScheduleEntry
from src.progena.schedules.validation import validate_schedule_document
from src.progena.sessions.context import reset_current_thread_id, set_current_thread_id
from src.progena.tasks.documents import (
    card_to_markdown,
    markdown_to_card,
    validate_kanban_structure,
)
from src.progena.workflows.documents import default_workflow_definition


class _FakeStateManager:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {"mcpServers": {}}
        self.saved: list[dict[str, Any]] = []

    async def get_mcp_config(self) -> dict[str, Any]:
        return copy.deepcopy(self.payload)

    async def save_mcp_config(self, payload: dict[str, Any]) -> None:
        self.payload = copy.deepcopy(payload)
        self.saved.append(copy.deepcopy(payload))


def _backend(payload=None) -> tuple[StateDocumentBackend, _FakeStateManager, list[bool]]:
    sm = _FakeStateManager(payload)
    reloads: list[bool] = []

    async def on_change() -> None:
        reloads.append(True)

    backend = StateDocumentBackend([create_mcp_state_file(sm, on_change=on_change)])
    return backend, sm, reloads


class _FakeKnowledgeStateManager:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {"bases": []}
        self.saved: list[dict[str, Any]] = []

    async def get_knowledge_config(self) -> dict[str, Any]:
        return copy.deepcopy(self.payload)

    async def save_knowledge_config(self, payload: dict[str, Any]) -> None:
        self.payload = copy.deepcopy(payload)
        self.saved.append(copy.deepcopy(payload))


def _knowledge_backend(
    payload: dict[str, Any] | None = None,
) -> tuple[StateDocumentBackend, _FakeKnowledgeStateManager, list[bool]]:
    sm = _FakeKnowledgeStateManager(payload)
    reloads: list[bool] = []

    async def on_change() -> None:
        reloads.append(True)

    backend = StateDocumentBackend([create_knowledge_state_file(sm, on_change=on_change)])
    return backend, sm, reloads


class _FakeModelProfilesStateManager:
    def __init__(self) -> None:
        self.payload = {"general": {"provider_id": "foundry-1", "deployment": "general-v1"}}
        self.saved: list[dict[str, Any]] = []

    async def get_model_profiles_config(self) -> dict[str, Any]:
        return copy.deepcopy(self.payload)

    async def save_model_profiles_config(self, payload: dict[str, Any]) -> None:
        self.payload = copy.deepcopy(payload)
        self.saved.append(copy.deepcopy(payload))


@pytest.mark.asyncio
async def test_model_profiles_file_adds_edits_and_removes_profiles_for_the_agent():
    state_manager = _FakeModelProfilesStateManager()
    backend = StateDocumentBackend([create_model_profiles_state_file(state_manager)])

    assert (await backend.aread("/profile.json")).error is not None

    created = await backend.awrite(
        "/profiles.json",
        json.dumps(
            {
                "general": {"provider_id": "foundry-1", "deployment": "general-v1"},
                "review": {"provider_id": "foundry-1", "deployment": "review-v1"},
            }
        ),
    )
    assert created.error is None
    assert state_manager.saved[-1]["review"]["deployment"] == "review-v1"

    edited = await backend.aedit("/profiles.json", "review-v1", "review-v2")
    assert edited.error is None
    assert edited.occurrences == 1
    assert state_manager.payload["review"]["deployment"] == "review-v2"

    removed = await backend.awrite(
        "/profiles.json",
        json.dumps({"general": {"provider_id": "foundry-1", "deployment": "general-v1"}}),
    )
    assert removed.error is None
    assert "review" not in state_manager.payload


# ---- validation ------------------------------------------------------------


def test_validate_mcp_document_accepts_valid():
    doc = validate_mcp_document('{"mcpServers": {"jina": {"url": "https://x/mcp"}}}')
    assert doc["mcpServers"]["jina"]["url"] == "https://x/mcp"


def test_validate_mcp_document_rejects_bad_json():
    try:
        validate_mcp_document("{not json")
    except ValueError as exc:
        assert "JSON" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_validate_mcp_document_rejects_missing_url_and_bad_transport():
    for bad in (
        '{"mcpServers": {"x": {}}}',
        '{"mcpServers": {"x": {"url": "https://x", "transport": "sse"}}}',
        '{"nope": {}}',
    ):
        try:
            validate_mcp_document(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad}")


# ---- read / ls -------------------------------------------------------------


async def test_aread_returns_mcp_json():
    backend, _sm, _ = _backend({"mcpServers": {"jina": {"url": "https://x/mcp"}}})
    result = await backend.aread("/mcp.json")
    assert result.error is None
    content = result.file_data["content"]
    assert json.loads(content)["mcpServers"]["jina"]["url"] == "https://x/mcp"


async def test_aread_unknown_file_errors():
    backend, _sm, _ = _backend()
    result = await backend.aread("/nope.json")
    assert result.error and "not found" in result.error


async def test_als_lists_state_files():
    backend, _sm, _ = _backend()
    result = await backend.als("/")
    paths = [e["path"] for e in result.entries]
    assert "/mcp.json" in paths


# ---- write -----------------------------------------------------------------


async def test_awrite_persists_and_runs_on_change():
    backend, sm, reloads = _backend({"mcpServers": {}})
    new = json.dumps({"mcpServers": {"jina": {"url": "https://new/mcp", "transport": "http"}}})
    result = await backend.awrite("/mcp.json", new)
    assert result.error is None
    assert result.path == "/mcp.json"
    assert sm.payload["mcpServers"]["jina"]["url"] == "https://new/mcp"
    assert reloads == [True]


async def test_awrite_invalid_does_not_persist_or_reload():
    backend, sm, reloads = _backend({"mcpServers": {"jina": {"url": "https://x/mcp"}}})
    result = await backend.awrite("/mcp.json", '{"mcpServers": {"x": {}}}')  # missing url
    assert result.error and "Invalid" in result.error
    assert sm.saved == []
    assert reloads == []


async def test_knowledge_virtual_file_roundtrip() -> None:
    backend, sm, reloads = _knowledge_backend()
    result = await backend.awrite(
        "/knowledge.json",
        json.dumps({"bases": [{"name": "Project Docs", "tags": ["internal"]}]}),
    )
    assert result.error is None
    assert result.path == "/knowledge.json"
    assert sm.payload["bases"][0]["name"] == "Project Docs"
    assert sm.payload["bases"][0]["id"]
    assert reloads == [True]

    read = await backend.aread("/knowledge.json")
    assert read.error is None
    assert json.loads(read.file_data["content"]) == sm.payload


async def test_invalid_knowledge_virtual_file_does_not_persist() -> None:
    backend, sm, reloads = _knowledge_backend()
    result = await backend.awrite(
        "/knowledge.json",
        json.dumps({"bases": [{"id": "Not A Slug", "name": "Docs"}]}),
    )
    assert result.error and "Invalid" in result.error
    assert sm.saved == []
    assert reloads == []


# ---- edit ------------------------------------------------------------------


async def test_aedit_replaces_and_persists():
    backend, sm, reloads = _backend({"mcpServers": {"jina": {"url": "https://old/mcp"}}})
    result = await backend.aedit("/mcp.json", "https://old/mcp", "https://new/mcp")
    assert result.error is None
    assert result.occurrences == 1
    assert sm.payload["mcpServers"]["jina"]["url"] == "https://new/mcp"
    assert reloads == [True]


async def test_aedit_to_invalid_is_rejected():
    backend, sm, reloads = _backend({"mcpServers": {"jina": {"url": "https://old/mcp"}}})
    # Replace the url value with empty string -> server without url -> invalid.
    result = await backend.aedit("/mcp.json", '"https://old/mcp"', '""')
    assert result.error and "invalid" in result.error.lower()
    assert sm.saved == []
    assert reloads == []


# ---- kanban (per-task directory) -------------------------------------------


class _FakeKanbanStateManager:
    """Mimic the StateManager per-card kanban surface over in-memory dicts."""

    def __init__(self, cards: list[dict[str, Any]] | None = None) -> None:
        self.cards: dict[str, dict[str, Any]] = {}
        self.structure: dict[str, Any] = {
            "id": "progena-kanban",
            "columns": [],
            "card_order": [],
        }
        for card in cards or []:
            self.cards[card["id"]] = copy.deepcopy(card)
            self.structure["card_order"].append(card["id"])

    async def get_kanban_structure(self) -> dict[str, Any]:
        return copy.deepcopy(self.structure)

    async def save_kanban_structure(self, structure: dict[str, Any]) -> None:
        self.structure = copy.deepcopy(structure)

    async def list_kanban_card_ids(self) -> list[str]:
        return list(self.structure.get("card_order", []))

    async def get_kanban_card(self, card_id: str) -> dict[str, Any] | None:
        card = self.cards.get(card_id)
        return copy.deepcopy(card) if card is not None else None

    async def save_kanban_card(self, card_id: str, card: dict[str, Any]) -> None:
        self.cards[card_id] = copy.deepcopy(card)
        if card_id not in self.structure["card_order"]:
            self.structure["card_order"].append(card_id)

    async def delete_kanban_card(self, card_id: str) -> None:
        self.cards.pop(card_id, None)
        self.structure["card_order"] = [
            cid for cid in self.structure["card_order"] if cid != card_id
        ]

    async def get_kanban_board(self) -> dict[str, Any]:
        cards = [
            copy.deepcopy(self.cards[cid])
            for cid in self.structure.get("card_order", [])
            if cid in self.cards
        ]
        board = {k: v for k, v in self.structure.items() if k != "card_order"}
        board["cards"] = cards
        return board


def _kanban_backend(cards=None) -> tuple[KanbanTaskDirectoryBackend, _FakeKanbanStateManager]:
    sm = _FakeKanbanStateManager(cards)
    return KanbanTaskDirectoryBackend(sm), sm


def test_validate_kanban_structure_accepts_valid():
    doc = validate_kanban_structure('{"columns": [{"id": "backlog"}], "card_order": ["card_1"]}')
    assert doc["card_order"] == ["card_1"]


def test_validate_kanban_structure_rejects_bad_shapes():
    for bad in (
        "{not json",
        '{"columns": {}}',  # columns must be a list
        '{"card_order": {}}',  # card_order must be a list
        '{"cards": []}',  # cards live in per-task files, not board.json
        "[]",  # top level must be object
    ):
        try:
            validate_kanban_structure(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad}")


def test_card_markdown_round_trips():
    card = {
        "id": "card_1",
        "title": "Ship",
        "column_id": "backlog",
        "description": "Do the thing",
        "repo_id": "progena",
        "acp_task_id": "task_123",
    }
    text = card_to_markdown(card)
    assert text.startswith("---\n")
    parsed = markdown_to_card(text, "card_1")
    assert parsed["title"] == "Ship"
    assert parsed["column_id"] == "backlog"
    assert parsed["description"] == "Do the thing"
    assert parsed["repo_id"] == "progena"
    assert parsed["acp_task_id"] == "task_123"


async def test_kanban_aread_and_ls():
    backend, _sm = _kanban_backend([{"id": "card_1", "title": "Ship", "column_id": "backlog"}])
    listed = await backend.als("/")
    paths = [entry["path"] for entry in listed.entries]
    assert "/board.json" in paths
    assert "/card_1.md" in paths
    result = await backend.aread("/card_1.md")
    assert result.error is None
    assert markdown_to_card(result.file_data["content"], "card_1")["title"] == "Ship"


async def test_kanban_board_json_reads_structure():
    backend, _sm = _kanban_backend([{"id": "card_1", "title": "Ship"}])
    result = await backend.aread("/board.json")
    assert result.error is None
    assert json.loads(result.file_data["content"])["card_order"] == ["card_1"]


async def test_kanban_awrite_persists_new_card():
    backend, sm = _kanban_backend([])
    md = "---\ntitle: Agent-made task\ncolumn_id: backlog\n---\n\nBody text\n"
    result = await backend.awrite("/card_9.md", md)
    assert result.error is None
    assert sm.cards["card_9"]["title"] == "Agent-made task"
    assert sm.cards["card_9"]["description"] == "Body text"
    # New card is auto-tracked in the board's card_order.
    assert "card_9" in sm.structure["card_order"]


async def test_kanban_awrite_invalid_does_not_persist():
    backend, sm = _kanban_backend([])
    result = await backend.awrite("/card_9.md", "---\nowner: user\n---\n\nNo title here\n")
    assert result.error and "Invalid" in result.error
    assert sm.cards == {}


async def test_kanban_aedit_updates_single_card():
    backend, sm = _kanban_backend(
        [{"id": "card_1", "title": "Ship", "column_id": "backlog", "description": "old"}]
    )
    result = await backend.aedit("/card_1.md", "old", "new")
    assert result.error is None
    assert sm.cards["card_1"]["description"] == "new"


async def test_kanban_adelete_removes_card():
    backend, sm = _kanban_backend([{"id": "card_1", "title": "Ship"}])
    result = await backend.adelete("/card_1.md")
    assert result.error is None
    assert "card_1" not in sm.cards
    assert "card_1" not in sm.structure["card_order"]


# ---- kanban (project-scoped directories) ------------------------------------


async def test_kanban_als_groups_by_project():
    backend, _sm = _kanban_backend(
        [
            {"id": "card_1", "title": "Unscoped"},
            {"id": "card_2", "title": "Scoped", "project_id": "acme"},
        ]
    )
    listed = await backend.als("/")
    paths = {entry["path"] for entry in listed.entries}
    assert paths == {"/board.json", "/card_1.md", "/acme/"}
    assert next(e for e in listed.entries if e["path"] == "/acme/")["is_dir"] is True

    scoped = await backend.als("/acme/")
    assert [entry["path"] for entry in scoped.entries] == ["/acme/card_2.md"]


async def test_kanban_aread_project_scoped_card():
    backend, _sm = _kanban_backend([{"id": "card_2", "title": "Scoped", "project_id": "acme"}])
    result = await backend.aread("/acme/card_2.md")
    assert result.error is None
    assert markdown_to_card(result.file_data["content"], "card_2", "acme")["title"] == "Scoped"
    # Wrong (or missing) project prefix doesn't resolve to the same card.
    assert (await backend.aread("/card_2.md")).error is not None
    assert (await backend.aread("/other/card_2.md")).error is not None


async def test_kanban_awrite_new_card_under_project_stamps_project_id():
    backend, sm = _kanban_backend([])
    md = "---\ntitle: New in project\ncolumn_id: backlog\n---\n"
    result = await backend.awrite("/acme/card_9.md", md)
    assert result.error is None
    assert sm.cards["card_9"]["project_id"] == "acme"


async def test_kanban_awrite_moves_card_between_projects():
    backend, sm = _kanban_backend([{"id": "card_1", "title": "Ship", "project_id": "acme"}])
    md = "---\ntitle: Ship\n---\n"
    result = await backend.awrite("/beta/card_1.md", md)
    assert result.error is None
    assert sm.cards["card_1"]["project_id"] == "beta"
    # The old project path no longer resolves once the card has moved.
    assert (await backend.aread("/acme/card_1.md")).error is not None


async def test_kanban_reserved_project_segments_do_not_resolve():
    backend, _sm = _kanban_backend([{"id": "card_1", "title": "Ship", "project_id": ".."}])
    # A card can't actually be scoped to "." or ".." - those paths never resolve,
    # even to a card whose stored project_id happens to be that literal string.
    assert (await backend.aread("/../card_1.md")).error is not None
    assert (await backend.aread("/./card_1.md")).error is not None
    assert (await backend.aread("/ /card_1.md")).error is not None


async def test_kanban_adelete_project_scoped_requires_matching_path():
    backend, sm = _kanban_backend([{"id": "card_1", "title": "Ship", "project_id": "acme"}])
    mismatched = await backend.adelete("/card_1.md")
    assert mismatched.error is not None
    assert "card_1" in sm.cards
    result = await backend.adelete("/acme/card_1.md")
    assert result.error is None
    assert "card_1" not in sm.cards


# ---- inbox (per-message directory) -------------------------------------------


class _FakeInboxStateManager:
    """Mimic the StateManager per-message inbox surface over an in-memory dict."""

    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self.messages: dict[str, dict[str, Any]] = {
            message["id"]: copy.deepcopy(message) for message in messages or []
        }

    async def get_inbox_message(self, message_id: str) -> dict[str, Any] | None:
        message = self.messages.get(message_id)
        return copy.deepcopy(message) if message is not None else None

    async def save_inbox_message(self, message_id: str, message: dict[str, Any]) -> None:
        self.messages[message_id] = copy.deepcopy(message)

    async def delete_inbox_message(self, message_id: str) -> None:
        self.messages.pop(message_id, None)

    async def list_inbox_messages(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(message) for message in self.messages.values()]


def _inbox_backend(messages=None) -> tuple[InboxDirectoryBackend, _FakeInboxStateManager]:
    sm = _FakeInboxStateManager(messages)
    return InboxDirectoryBackend(sm), sm


def test_inbox_message_markdown_round_trips():
    message = {
        "id": "msg_1",
        "title": "Digest",
        "created_at": "2026-07-05T00:00:00+00:00",
        "read": False,
        "read_at": None,
        "body": "Line one\n\n**bold**",
    }
    text = inbox_message_to_markdown(message)
    assert text.startswith("---\n")
    parsed = markdown_to_inbox_message(text, "msg_1")
    assert parsed == message


def test_markdown_to_inbox_message_stamps_created_at_and_coerces_read():
    parsed = markdown_to_inbox_message("---\ntitle: Hello\n---\n\nBody\n", "msg_1")
    assert parsed["id"] == "msg_1"
    assert parsed["read"] is False
    assert parsed["read_at"] is None
    assert parsed["created_at"]  # stamped when absent


def test_markdown_to_inbox_message_filename_id_wins():
    parsed = markdown_to_inbox_message("---\ntitle: Hello\nid: other\n---\n", "msg_1")
    assert parsed["id"] == "msg_1"


def test_markdown_to_inbox_message_stamps_thread_id_from_context():
    from src.progena.sessions.context import reset_current_thread_id, set_current_thread_id

    token = set_current_thread_id("thread_123")
    try:
        parsed = markdown_to_inbox_message("---\ntitle: Hello\n---\n\nBody\n", "msg_1")
    finally:
        reset_current_thread_id(token)
    assert parsed["thread_id"] == "thread_123"


def test_markdown_to_inbox_message_no_thread_id_when_no_context():
    parsed = markdown_to_inbox_message("---\ntitle: Hello\n---\n\nBody\n", "msg_1")
    assert "thread_id" not in parsed


def test_markdown_to_inbox_message_preserves_existing_thread_id_on_edit():
    from src.progena.sessions.context import reset_current_thread_id, set_current_thread_id

    token = set_current_thread_id("thread_new")
    try:
        parsed = markdown_to_inbox_message(
            "---\ntitle: Hello\nthread_id: thread_original\n---\n\nBody\n", "msg_1"
        )
    finally:
        reset_current_thread_id(token)
    assert parsed["thread_id"] == "thread_original"


def test_markdown_to_inbox_message_requires_title():
    try:
        markdown_to_inbox_message("---\nread: false\n---\n\nNo title\n", "msg_1")
    except ValueError as exc:
        assert "title" in str(exc)
    else:
        raise AssertionError("expected ValueError")


async def test_inbox_awrite_persists_and_aread_round_trips():
    backend, sm = _inbox_backend([])
    result = await backend.awrite("/msg_1.md", "---\ntitle: Digest\n---\n\nBody text\n")
    assert result.error is None
    assert sm.messages["msg_1"]["title"] == "Digest"
    assert sm.messages["msg_1"]["body"] == "Body text"
    read = await backend.aread("/msg_1.md")
    assert read.error is None
    assert "Body text" in read.file_data["content"]


async def test_inbox_awrite_invalid_does_not_persist():
    backend, sm = _inbox_backend([])
    result = await backend.awrite("/msg_1.md", "---\nread: true\n---\n\nNo title\n")
    assert result.error and "Invalid" in result.error
    assert sm.messages == {}


async def test_inbox_awrite_rejects_nested_and_non_md_paths():
    backend, sm = _inbox_backend([])
    for path in ("/sub/msg_1.md", "/msg_1.txt"):
        result = await backend.awrite(path, "---\ntitle: Hello\n---\n")
        assert result.error is not None
    assert sm.messages == {}


async def test_inbox_aread_unknown_errors():
    backend, _sm = _inbox_backend([])
    result = await backend.aread("/nope.md")
    assert result.error and "not found" in result.error


async def test_inbox_als_lists_newest_first():
    backend, _sm = _inbox_backend(
        [
            {"id": "msg_old", "title": "Old", "created_at": "2026-07-01T00:00:00+00:00"},
            {"id": "msg_new", "title": "New", "created_at": "2026-07-05T00:00:00+00:00"},
        ]
    )
    listed = await backend.als("/")
    assert [entry["path"] for entry in listed.entries] == ["/msg_new.md", "/msg_old.md"]


async def test_inbox_aedit_updates_body():
    backend, sm = _inbox_backend(
        [
            {
                "id": "msg_1",
                "title": "Digest",
                "created_at": "2026-07-05T00:00:00+00:00",
                "body": "old",
            }
        ]
    )
    result = await backend.aedit("/msg_1.md", "old", "new")
    assert result.error is None
    assert sm.messages["msg_1"]["body"] == "new"


async def test_inbox_adelete_removes_message():
    backend, sm = _inbox_backend([{"id": "msg_1", "title": "Digest"}])
    result = await backend.adelete("/msg_1.md")
    assert result.error is None
    assert sm.messages == {}
    missing = await backend.adelete("/msg_1.md")
    assert missing.error is not None


# ---- workflows (per-workflow JSON directory) ---------------------------------


class _FakeWorkflowStateManager:
    def __init__(
        self,
        workflows: list[dict[str, Any]] | None = None,
        prompts: dict[str, str] | None = None,
    ) -> None:
        self.workflows: dict[str, dict[str, Any]] = {
            workflow["id"]: copy.deepcopy(workflow) for workflow in workflows or []
        }
        self.prompts = dict(prompts or {})

    async def list_content_documents(self, kind: str) -> list[tuple[str, str]]:
        assert kind == "instruction"
        return sorted(self.prompts.items())

    async def get_preferences_config(self) -> dict[str, str]:
        return {"timezone": "Australia/Melbourne"}

    async def list_routine_graphs(self) -> list[dict[str, Any]]:
        return []

    async def get_workflow_definition(self, workflow_id: str) -> dict[str, Any] | None:
        workflow = self.workflows.get(workflow_id)
        return copy.deepcopy(workflow) if workflow is not None else None

    async def save_workflow_definition(self, workflow_id: str, workflow: dict[str, Any]) -> None:
        self.workflows[workflow_id] = copy.deepcopy(workflow)

    async def delete_workflow_definition(self, workflow_id: str) -> None:
        self.workflows.pop(workflow_id, None)

    async def list_workflow_definitions(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(workflow) for workflow in self.workflows.values()]

    async def list_document_types(self) -> dict[str, Any]:
        from src.progena.documents.types import BUILTIN_DOCUMENT_TYPES

        return {t.id: t for t in BUILTIN_DOCUMENT_TYPES}


def _workflow_backend(
    workflows=None, prompts=None
) -> tuple[WorkflowDirectoryBackend, _FakeWorkflowStateManager]:
    sm = _FakeWorkflowStateManager(workflows, prompts)
    return WorkflowDirectoryBackend(sm), sm


async def test_workflow_aread_and_ls():
    workflow = default_workflow_definition(
        workflow_id="customer-followup", title="Customer follow-up"
    )
    backend, _sm = _workflow_backend([workflow])
    listed = await backend.als("/")
    assert [entry["path"] for entry in listed.entries] == ["/customer-followup.json"]
    read = await backend.aread("/customer-followup.json")
    assert read.error is None
    assert json.loads(read.file_data["content"])["title"] == "Customer follow-up"


async def test_workflow_awrite_persists_valid_definition():
    workflow = default_workflow_definition(
        workflow_id="customer-followup", title="Customer follow-up"
    )
    backend, sm = _workflow_backend([])
    result = await backend.awrite("/customer-followup.json", json.dumps(workflow))
    assert result.error is None
    assert sm.workflows["customer-followup"]["title"] == "Customer follow-up"


@pytest.mark.parametrize("timezone", [None, ""])
async def test_workflow_awrite_stamps_new_null_or_empty_cron_timezone(timezone):
    workflow = default_workflow_definition(workflow_id="daily", title="Daily")
    cron_id = next(
        node_id for node_id, node in workflow["graph"]["nodes"].items() if node["kind"] == "event"
    )
    agent_id = next(
        node_id for node_id, node in workflow["graph"]["nodes"].items() if node["kind"] == "agent"
    )
    workflow["graph"]["nodes"][cron_id] = {
        "kind": "cron",
        "title": "Daily",
        "expression": "0 9 * * *",
        "timezone": timezone,
    }
    workflow["graph"]["nodes"][agent_id]["template"] = "Run at {scheduled_for}"
    workflow["graph"]["edges"] = [
        {
            "from": {"node": cron_id, "port": "scheduled_for"},
            "to": {"node": agent_id, "port": "scheduled_for"},
        }
    ]
    backend, sm = _workflow_backend([])

    result = await backend.awrite("/daily.json", json.dumps(workflow))

    assert result.error is None
    assert sm.workflows["daily"]["graph"]["nodes"][cron_id]["timezone"] == "Australia/Melbourne"


async def test_workflow_awrite_resolves_prompt_document_references():
    workflow = default_workflow_definition(workflow_id="card-brief", title="Card brief")
    agent_id = next(i for i, n in workflow["graph"]["nodes"].items() if n["kind"] == "agent")
    agent = workflow["graph"]["nodes"][agent_id]
    agent.pop("template")
    agent["instruction"] = "workflows/brief.md"
    backend, sm = _workflow_backend(prompts={"workflows/brief.md": "Brief the card {key}."})

    result = await backend.awrite("/card-brief.json", json.dumps(workflow))

    assert result.error is None
    assert (
        sm.workflows["card-brief"]["graph"]["nodes"][agent_id]["instruction"]
        == "workflows/brief.md"
    )

    agent["instruction"] = "/instructions/workflows/brief.md"
    result = await backend.awrite("/card-brief.json", json.dumps(workflow))
    assert result.error and "Invalid instruction path" in result.error


async def test_workflow_awrite_keeps_existing_unstamped_cron_timezone_omitted():
    workflow = default_workflow_definition(workflow_id="daily", title="Daily")
    cron_id = next(
        node_id for node_id, node in workflow["graph"]["nodes"].items() if node["kind"] == "event"
    )
    agent_id = next(
        node_id for node_id, node in workflow["graph"]["nodes"].items() if node["kind"] == "agent"
    )
    workflow["graph"]["nodes"][cron_id] = {
        "kind": "cron",
        "title": "Daily",
        "expression": "0 9 * * *",
    }
    workflow["graph"]["nodes"][agent_id]["template"] = "Run at {scheduled_for}"
    workflow["graph"]["edges"] = [
        {
            "from": {"node": cron_id, "port": "scheduled_for"},
            "to": {"node": agent_id, "port": "scheduled_for"},
        }
    ]
    backend, sm = _workflow_backend([workflow])

    result = await backend.awrite("/daily.json", json.dumps(workflow))

    assert result.error is None
    assert "timezone" not in sm.workflows["daily"]["graph"]["nodes"][cron_id]


async def test_workflow_awrite_rejects_filename_id_mismatch():
    workflow = default_workflow_definition(
        workflow_id="customer-followup", title="Customer follow-up"
    )
    workflow["id"] = "wrong-id"
    backend, sm = _workflow_backend([])
    result = await backend.awrite("/customer-followup.json", json.dumps(workflow))
    assert result.error and "must match filename" in result.error
    assert sm.workflows == {}


async def test_workflow_aedit_and_adelete_round_trip():
    workflow = default_workflow_definition(
        workflow_id="customer-followup", title="Customer follow-up"
    )
    backend, sm = _workflow_backend([workflow])
    edited = await backend.aedit(
        "/customer-followup.json", "Customer follow-up", "Renewal follow-up"
    )
    assert edited.error is None
    assert sm.workflows["customer-followup"]["title"] == "Renewal follow-up"
    deleted = await backend.adelete("/customer-followup.json")
    assert deleted.error is None
    assert sm.workflows == {}


# ---- routines (per-agent JSON directory, mirrors workflows) ------------------


class _FakeRoutineStateManager:
    async def get_execution_policy(self, policy_id):
        return {"id": policy_id}

    def __init__(self, graphs: list[dict[str, Any]] | None = None) -> None:
        self.graphs: dict[str, dict[str, Any]] = {
            graph["id"]: copy.deepcopy(graph) for graph in graphs or []
        }

    async def get_routine_graph(self, agent_id: str) -> dict[str, Any] | None:
        graph = self.graphs.get(agent_id)
        return copy.deepcopy(graph) if graph is not None else None

    async def save_routine_graph(self, agent_id: str, graph: dict[str, Any]) -> None:
        self.graphs[agent_id] = copy.deepcopy(graph)

    async def delete_routine_graph(self, agent_id: str) -> None:
        self.graphs.pop(agent_id, None)

    async def list_routine_graphs(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(graph) for graph in self.graphs.values()]


def _routine_backend(graphs=None) -> tuple[RoutineDirectoryBackend, _FakeRoutineStateManager]:
    sm = _FakeRoutineStateManager(graphs)
    return RoutineDirectoryBackend(sm), sm


async def test_routine_aread_and_ls():
    graph = default_routine_graph("subagent-example", name="Example Agent")
    backend, _sm = _routine_backend([graph])
    listed = await backend.als("/")
    assert [entry["path"] for entry in listed.entries] == ["/subagent-example.json"]
    read = await backend.aread("/subagent-example.json")
    assert read.error is None
    assert json.loads(read.file_data["content"])["name"] == "Example Agent"


async def test_routine_awrite_persists_valid_graph():
    graph = default_routine_graph("subagent-example", name="Example Agent")
    backend, sm = _routine_backend([])
    result = await backend.awrite("/subagent-example.json", json.dumps(graph))
    assert result.error is None
    assert sm.graphs["subagent-example"]["name"] == "Example Agent"


async def test_routine_awrite_rejects_filename_id_mismatch():
    graph = default_routine_graph("subagent-example", name="Example Agent")
    graph["id"] = "wrong-id"
    backend, sm = _routine_backend([])
    result = await backend.awrite("/subagent-example.json", json.dumps(graph))
    assert result.error and "must match filename" in result.error
    assert sm.graphs == {}


async def test_routine_awrite_rejects_invalid_schema():
    backend, sm = _routine_backend([])
    result = await backend.awrite("/subagent-example.json", json.dumps({"id": "subagent-example"}))
    assert result.error is not None
    assert sm.graphs == {}


async def test_routine_aedit_and_adelete_round_trip():
    graph = default_routine_graph("subagent-example", name="Example Agent")
    backend, sm = _routine_backend([graph])
    edited = await backend.aedit("/subagent-example.json", "Example Agent", "Watcher Agent")
    assert edited.error is None
    assert sm.graphs["subagent-example"]["name"] == "Watcher Agent"
    deleted = await backend.adelete("/subagent-example.json")
    assert deleted.error is None
    assert sm.graphs == {}


# ---- schedule (single file, object keyed by id) ------------------------------


class _FakeScheduleStateManager:
    def __init__(
        self, entries: list[ScheduleEntry] | None = None, timezone: str = "Australia/Melbourne"
    ) -> None:
        self.entries: dict[str, ScheduleEntry] = {e.id: e for e in entries or []}
        self.timezone = timezone

    async def get_schedule(self) -> dict[str, ScheduleEntry]:
        return dict(self.entries)

    async def get_preferences_config(self) -> dict[str, str]:
        return {"timezone": self.timezone}

    async def save_schedule(self, schedule: dict[str, ScheduleEntry]) -> None:
        self.entries = dict(schedule)


def _schedule_backend(
    entries=None,
) -> tuple[StateDocumentBackend, _FakeScheduleStateManager]:
    sm = _FakeScheduleStateManager(entries)
    backend = StateDocumentBackend([create_schedule_state_file(sm)])
    return backend, sm


def test_validate_schedule_document_accepts_valid():
    doc = validate_schedule_document(
        '{"sched_1": {"message": "do it", "cron_expression": "0 9 * * *"}}'
    )
    assert doc["sched_1"]["message"] == "do it"


def test_validate_schedule_document_rejects_bad_shapes():
    for bad in (
        "{not json",
        "[]",  # top level must be an object
        '{"sched_1": "not an object"}',
        '{"sched_1": {}}',  # message is required
    ):
        try:
            validate_schedule_document(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad}")


def test_validate_schedule_document_rejects_invalid_cron_expression():
    with pytest.raises(ValueError, match="cron_expression"):
        validate_schedule_document(
            '{"sched_1": {"message": "do it", "cron_expression": "not a cron"}}'
        )


def test_validate_schedule_document_rejects_invalid_scheduled_at():
    with pytest.raises(ValueError, match="scheduled_at"):
        validate_schedule_document(
            '{"sched_1": {"message": "do it", "scheduled_at": "not a timestamp"}}'
        )


def test_validate_schedule_document_rejects_non_string_message():
    # A non-string message coerces to a truthy str() under the old hand-rolled
    # check, then blows up pydantic.ValidationError inside ScheduleEntry(...)
    # at save() time. It must now be rejected here, at validate() time.
    with pytest.raises(ValueError):
        validate_schedule_document('{"sched_1": {"message": 123, "cron_expression": "0 9 * * *"}}')


def test_validate_schedule_document_rejects_both_cron_and_scheduled_at():
    with pytest.raises(ValueError, match="exactly one"):
        validate_schedule_document(
            '{"sched_1": {"message": "do it", "cron_expression": "0 9 * * *", '
            '"scheduled_at": "2030-01-01T00:00:00Z"}}'
        )


def test_validate_schedule_document_rejects_neither_cron_nor_scheduled_at():
    with pytest.raises(ValueError, match="exactly one"):
        validate_schedule_document('{"sched_1": {"message": "do it"}}')


def test_validate_mcp_document_rejects_schema_invalid_extra_field():
    # mcp_server_entry.schema.v1.json sets additionalProperties: false; the
    # old hand-rolled checks only ever look at url/transport, so an unknown
    # field like this would have passed them alone.
    with pytest.raises(ValueError, match="schema"):
        validate_mcp_document('{"mcpServers": {"x": {"url": "https://x/mcp", "bogus_field": "z"}}}')


def test_validate_registry_document_rejects_schema_invalid_field_type():
    # agent_entry.schema.v1.json types 'enabled' as a boolean; the old
    # hand-rolled checks never look at 'enabled' at all.
    with pytest.raises(ValueError, match="schema"):
        validate_registry_document('{"agents": [{"name": "x", "enabled": "yes"}], "tools": []}')


async def test_schedule_aread_and_ls():
    backend, _sm = _schedule_backend(
        [ScheduleEntry(id="sched_1", message="do it", cron_expression="0 9 * * *")]
    )
    listed = await backend.als("/")
    paths = [entry["path"] for entry in listed.entries]
    assert paths == ["/schedule.json"]
    result = await backend.aread("/schedule.json")
    assert result.error is None
    doc = json.loads(result.file_data["content"])
    assert doc["sched_1"]["message"] == "do it"
    assert "id" not in doc["sched_1"]


async def test_schedule_aread_unknown_errors():
    backend, _sm = _schedule_backend([])
    result = await backend.aread("/nope.json")
    assert result.error and "not found" in result.error


async def test_schedule_awrite_persists_new_entry():
    backend, sm = _schedule_backend([])
    body = json.dumps({"sched_9": {"message": "new task", "cron_expression": "0 8 * * *"}})
    result = await backend.awrite("/schedule.json", body)
    assert result.error is None
    assert sm.entries["sched_9"].id == "sched_9"
    assert sm.entries["sched_9"].message == "new task"
    assert sm.entries["sched_9"].model_extra["timezone"] == "Australia/Melbourne"


@pytest.mark.parametrize("timezone", [None, ""])
async def test_schedule_awrite_keeps_existing_null_or_empty_timezone(timezone):
    backend, sm = _schedule_backend(
        [
            ScheduleEntry(
                id="sched_9",
                message="existing task",
                cron_expression="0 8 * * *",
                timezone=timezone,
            )
        ]
    )
    body = json.dumps(
        {
            "sched_9": {
                "message": "updated task",
                "cron_expression": "0 8 * * *",
                "timezone": timezone,
            }
        }
    )

    result = await backend.awrite("/schedule.json", body)

    assert result.error is None
    assert sm.entries["sched_9"].model_extra["timezone"] == timezone


@pytest.mark.parametrize("timezone", [None, ""])
async def test_schedule_awrite_stamps_new_null_or_empty_timezone(timezone):
    backend, sm = _schedule_backend([])
    body = json.dumps(
        {
            "sched_9": {
                "message": "new task",
                "cron_expression": "0 8 * * *",
                "timezone": timezone,
            }
        }
    )

    result = await backend.awrite("/schedule.json", body)

    assert result.error is None
    assert sm.entries["sched_9"].model_extra["timezone"] == "Australia/Melbourne"


async def test_schedule_awrite_normalizes_naive_one_shot_timestamp_to_utc():
    backend, sm = _schedule_backend([])

    result = await backend.awrite(
        "/schedule.json",
        json.dumps({"cron_9": {"message": "remind me", "scheduled_at": "2026-06-19T09:47:00"}}),
    )

    assert result.error is None
    assert sm.entries["cron_9"].scheduled_at == "2026-06-19T09:47:00+00:00"


async def test_schedule_awrite_invalid_does_not_persist():
    backend, sm = _schedule_backend([])
    result = await backend.awrite("/schedule.json", '{"sched_9": {}}')  # missing message
    assert result.error and "Invalid" in result.error
    assert sm.entries == {}


async def test_schedule_aedit_updates_single_entry():
    backend, sm = _schedule_backend(
        [ScheduleEntry(id="sched_1", message="old", cron_expression="0 9 * * *")]
    )
    result = await backend.aedit("/schedule.json", '"old"', '"new"')
    assert result.error is None
    assert sm.entries["sched_1"].message == "new"


async def test_schedule_awrite_removes_entry():
    backend, sm = _schedule_backend(
        [ScheduleEntry(id="sched_1", message="do it", cron_expression="0 9 * * *")]
    )
    result = await backend.awrite("/schedule.json", "{}")
    assert result.error is None
    assert sm.entries == {}


# ---- schedule destination chat ------------------------------------------------


async def _awrite_in_thread(backend: StateDocumentBackend, thread_id: str | None, body: str):
    token = set_current_thread_id(thread_id)
    try:
        return await backend.awrite("/schedule.json", body)
    finally:
        reset_current_thread_id(token)


def _chat_schedule_backend(entries=None, chats=("conv_a",)):
    backend, sm = _schedule_backend(entries)

    async def get_session(thread_id: str):
        return {"thread_id": thread_id} if thread_id in chats else None

    sm.get_session = get_session
    return backend, sm


async def test_schedule_created_in_chat_delivers_to_that_chat():
    backend, sm = _chat_schedule_backend()
    body = json.dumps({"sched_1": {"message": "check", "cron_expression": "*/5 * * * *"}})
    result = await _awrite_in_thread(backend, "conv_a", body)
    assert result.error is None
    assert sm.entries["sched_1"].conversation_id == "conv_a"


async def test_schedule_created_outside_a_chat_is_standalone():
    backend, sm = _chat_schedule_backend()
    body = json.dumps({"sched_1": {"message": "check", "cron_expression": "*/5 * * * *"}})
    # Schedule-run threads have no session document, so they never become a destination.
    result = await _awrite_in_thread(backend, "sched_x_1234abcd", body)
    assert result.error is None
    assert sm.entries["sched_1"].conversation_id is None


async def test_schedule_explicit_null_stays_standalone_inside_a_chat():
    backend, sm = _chat_schedule_backend()
    body = json.dumps(
        {"sched_1": {"message": "check", "cron_expression": "*/5 * * * *", "conversation_id": None}}
    )
    result = await _awrite_in_thread(backend, "conv_a", body)
    assert result.error is None
    assert sm.entries["sched_1"].conversation_id is None


async def test_schedule_agent_cannot_assign_another_chat():
    backend, sm = _chat_schedule_backend(chats=("conv_a", "conv_b"))
    body = json.dumps(
        {
            "sched_1": {
                "message": "check",
                "cron_expression": "*/5 * * * *",
                "conversation_id": "conv_b",
            }
        }
    )
    result = await _awrite_in_thread(backend, "conv_a", body)
    assert result.error and "assigned by the runtime" in result.error
    assert sm.entries == {}


async def test_schedule_edit_keeps_destination_and_cannot_retarget():
    backend, sm = _chat_schedule_backend(
        [
            ScheduleEntry(
                id="sched_1",
                message="check",
                cron_expression="*/5 * * * *",
                conversation_id="conv_a",
            )
        ],
        chats=("conv_a", "conv_b"),
    )
    # Editing from a different chat keeps the original destination.
    body = json.dumps({"sched_1": {"message": "check again", "cron_expression": "*/5 * * * *"}})
    result = await _awrite_in_thread(backend, "conv_b", body)
    assert result.error is None
    assert sm.entries["sched_1"].conversation_id == "conv_a"
    assert sm.entries["sched_1"].message == "check again"

    retarget = json.dumps(
        {
            "sched_1": {
                "message": "check",
                "cron_expression": "*/5 * * * *",
                "conversation_id": "conv_b",
            }
        }
    )
    result = await _awrite_in_thread(backend, "conv_b", retarget)
    assert result.error and "cannot be changed" in result.error
    assert sm.entries["sched_1"].conversation_id == "conv_a"


# ---- registry (single whole-document file) -----------------------------------


class _FakeRegistryStateManager:
    def __init__(self, registry: dict[str, Any] | None = None) -> None:
        self.registry: dict[str, Any] = registry or {"agents": [], "tools": []}

    async def get_registry(self) -> dict[str, Any]:
        return copy.deepcopy(self.registry)

    async def save_registry(self, registry: dict[str, Any]) -> None:
        self.registry = copy.deepcopy(registry)


def _registry_backend(
    registry=None,
) -> tuple[StateDocumentBackend, _FakeRegistryStateManager]:
    sm = _FakeRegistryStateManager(registry)
    backend = StateDocumentBackend([create_registry_state_file(sm)])
    return backend, sm


def test_validate_registry_document_accepts_valid():
    doc = validate_registry_document(
        '{"agents": [{"name": "jina", "kind": "a2a_agent"}], "tools": []}'
    )
    assert doc["agents"][0]["name"] == "jina"


def test_validate_registry_document_rejects_bad_shapes():
    for bad in (
        "{not json",
        "[]",  # top level must be object
        '{"agents": [{}], "tools": []}',  # agent missing name
        '{"agents": []}',  # missing tools
        '{"tools": []}',  # missing agents
        '{"agents": [], "tools": [], "extra": 1}',  # unexpected top-level key
    ):
        try:
            validate_registry_document(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad}")


async def test_registry_aread_and_ls():
    backend, _sm = _registry_backend({"agents": [{"name": "jina"}], "tools": []})
    listed = await backend.als("/")
    paths = [entry["path"] for entry in listed.entries]
    assert "/agents.json" in paths
    result = await backend.aread("/agents.json")
    assert result.error is None
    assert json.loads(result.file_data["content"])["agents"][0]["name"] == "jina"


async def test_registry_awrite_persists_new_agent():
    backend, sm = _registry_backend({"agents": [], "tools": []})
    body = json.dumps({"agents": [{"name": "new-agent", "enabled": True}], "tools": []})
    result = await backend.awrite("/agents.json", body)
    assert result.error is None
    assert sm.registry["agents"][0]["name"] == "new-agent"


async def test_registry_awrite_invalid_does_not_persist():
    backend, sm = _registry_backend({"agents": [], "tools": []})
    result = await backend.awrite("/agents.json", '{"agents": [{}], "tools": []}')
    assert result.error and "Invalid" in result.error
    assert sm.registry == {"agents": [], "tools": []}


async def test_registry_aedit_updates_single_agent():
    backend, sm = _registry_backend({"agents": [{"name": "jina", "enabled": True}], "tools": []})
    result = await backend.aedit("/agents.json", '"enabled": true', '"enabled": false')
    assert result.error is None
    assert sm.registry["agents"][0]["enabled"] is False


class _FakeHealthStateManager:
    def __init__(self, health: dict[str, Any] | None = None) -> None:
        self.health: dict[str, Any] = health or {}

    async def get_health(self) -> dict[str, Any]:
        return copy.deepcopy(self.health)


def _health_backend(health=None) -> tuple[StateDocumentBackend, _FakeHealthStateManager]:
    sm = _FakeHealthStateManager(health)
    backend = StateDocumentBackend([create_health_state_file(sm)])
    return backend, sm


async def test_health_aread_returns_status():
    backend, _sm = _health_backend(
        {"status": "running", "cycle_count": 12, "version": "abc123def456"}
    )
    result = await backend.aread("/health.json")
    assert result.error is None
    content = json.loads(result.file_data["content"])
    assert content["status"] == "running"
    assert content["cycle_count"] == 12
    assert content["version"] == "abc123def456"


async def test_health_aread_defaults_to_empty_object():
    backend, _sm = _health_backend()
    result = await backend.aread("/health.json")
    assert result.error is None
    assert json.loads(result.file_data["content"]) == {}


async def test_health_awrite_is_rejected():
    backend, sm = _health_backend({"status": "running"})
    result = await backend.awrite("/health.json", '{"status": "stopped"}')
    assert result.error and "read-only" in result.error
    assert sm.health == {"status": "running"}


async def test_health_aedit_is_rejected():
    backend, sm = _health_backend({"status": "running"})
    result = await backend.aedit("/health.json", '"running"', '"stopped"')
    assert result.error and "read-only" in result.error
    assert sm.health == {"status": "running"}


# ---- mcp_tools.json (per-tool enable/disable) -------------------------------


class _FakeMcpToolsStateManager:
    def __init__(self) -> None:
        self.payload: dict[str, Any] = {
            "tools": [
                {
                    "name": "jina_search_web",
                    "server": "jina",
                    "original_name": "search_web",
                    "description": "Search the web",
                    "enabled": True,
                },
                {
                    "name": "jina_search_jina_blog",
                    "server": "jina",
                    "original_name": "search_jina_blog",
                    "description": "Search the Jina blog",
                    "enabled": True,
                },
            ],
            "servers": {"jina": {"status": "connected", "tool_count": 2}},
            "updated_at": "2026-10-11T00:00:00Z",
        }
        self.saved: list[dict[str, Any]] = []

    async def get_mcp_tools(self) -> dict[str, Any]:
        return copy.deepcopy(self.payload)

    async def save_mcp_tools(self, payload: dict[str, Any]) -> None:
        self.payload = copy.deepcopy(payload)
        self.saved.append(copy.deepcopy(payload))


def _mcp_tools_backend() -> tuple[StateDocumentBackend, _FakeMcpToolsStateManager]:
    sm = _FakeMcpToolsStateManager()
    return StateDocumentBackend([create_mcp_tools_state_file(sm)]), sm


async def _edit_tools(backend: StateDocumentBackend, mutate) -> Any:
    current = json.loads((await backend.aread("/mcp_tools.json", 0, 10_000)).file_data["content"])  # type: ignore[index]
    mutate(current)
    return await backend.awrite("/mcp_tools.json", json.dumps(current))


async def test_mcp_tools_edit_disables_one_tool_and_keeps_discovery_data():
    backend, sm = _mcp_tools_backend()
    result = await backend.aedit(
        "/mcp_tools.json",
        '"description": "Search the Jina blog",\n      "enabled": true',
        '"description": "Search the Jina blog",\n      "enabled": false',
    )
    assert result.error is None
    assert {tool["name"]: tool["enabled"] for tool in sm.payload["tools"]} == {
        "jina_search_web": True,
        "jina_search_jina_blog": False,
    }
    assert sm.payload["servers"] == {"jina": {"status": "connected", "tool_count": 2}}
    assert sm.payload["updated_at"] == "2026-10-11T00:00:00Z"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda doc: doc["tools"].pop(), "added or removed"),
        (lambda doc: doc["tools"].append({"name": "made_up", "enabled": True}), "added or removed"),
        (lambda doc: doc["tools"][0].update(description="hacked"), "only `enabled`"),
        (lambda doc: doc["tools"][0].update(enabled="no"), "does not conform to schema"),
        (lambda doc: doc["tools"][0].update(override=True), "does not conform to schema"),
        # Seen live: the agent added a top-level override map instead of
        # flipping the tool's flag; that must fail, not save as a no-op.
        (
            lambda doc: doc.update(tool_overrides={"jina_search_jina_blog": {"enabled": False}}),
            '`"enabled": false` on its entry',
        ),
        (lambda doc: doc["servers"]["jina"].update(status="error"), "only a tool's `enabled`"),
    ],
)
async def test_mcp_tools_rejects_anything_but_enabled_flags(mutate, message):
    backend, sm = _mcp_tools_backend()
    result = await _edit_tools(backend, mutate)
    assert result.error is not None and message in result.error
    assert sm.saved == []


# ---- grep / glob over the virtual documents -----------------------------------


async def test_grep_finds_text_on_the_line_read_file_shows():
    # Seen live: grep reported "No matches found" for a tool that was in
    # /config/mcp_tools.json, so the agent concluded it was missing and
    # invented a new key instead of flipping the tool's flag.
    backend, _sm = _mcp_tools_backend()
    result = await backend.agrep("jina_search_jina_blog")
    assert result.error is None
    [match] = result.matches or []
    assert match["path"] == "/mcp_tools.json"
    content = (await backend.aread("/mcp_tools.json", 0, 10_000)).file_data["content"]  # type: ignore[index]
    assert content.split("\n")[match["line"] - 1] == match["text"]
    assert '"name": "jina_search_jina_blog"' in match["text"]


async def test_grep_and_glob_walk_into_subdirectories():
    backend, _sm = _kanban_backend(
        [
            {"id": "card_1", "title": "Top level"},
            {"id": "card_2", "title": "Scoped needle", "project_id": "acme"},
        ]
    )
    grep = await backend.agrep("Scoped needle")
    assert [m["path"] for m in grep.matches or []] == ["/acme/card_2.md"]

    nested = await backend.aglob("**/*.md")
    assert sorted(f["path"] for f in nested.matches or []) == ["/acme/card_2.md", "/card_1.md"]
    top_only = await backend.aglob("*.md")
    assert [f["path"] for f in top_only.matches or []] == ["/card_1.md"]


async def test_grep_respects_path_and_glob_filters_and_misses_cleanly():
    backend, _sm = _kanban_backend(
        [
            {"id": "card_1", "title": "needle one"},
            {"id": "card_2", "title": "needle two", "project_id": "acme"},
        ]
    )
    scoped = await backend.agrep("needle", path="/acme")
    assert {m["path"] for m in scoped.matches or []} == {"/acme/card_2.md"}
    json_only = await backend.agrep("needle", glob="*.json")
    assert json_only.matches == []
    missing = await backend.agrep("not-there-anywhere")
    assert missing.error is None and missing.matches == []
