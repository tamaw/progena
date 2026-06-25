"""Tests for the DeepAgent state-document backend (state-as-files)."""

from __future__ import annotations

import copy
import json
from typing import Any

from app.progenitor.agent.state_backend import (
    KanbanTaskDirectoryBackend,
    StateDocumentBackend,
    card_to_markdown,
    create_mcp_state_file,
    markdown_to_card,
    validate_kanban_structure,
    validate_mcp_document,
)


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
            "id": "progenitor-kanban",
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
    card = {"id": "card_1", "title": "Ship", "column_id": "backlog", "description": "Do the thing"}
    text = card_to_markdown(card)
    assert text.startswith("---\n")
    parsed = markdown_to_card(text, "card_1")
    assert parsed["title"] == "Ship"
    assert parsed["column_id"] == "backlog"
    assert parsed["description"] == "Do the thing"


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
