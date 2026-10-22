"""Backend surfacing of MCP discovery state.

Covers two behaviours added for the live Tools page:
- A server whose discovery failed (e.g. auth error) is shown as ``error`` with a
  reason + an activity log, instead of a silently-empty "running" server.
- The MCP change fingerprint includes the discovered-tools document, so tool
  discovery completing after a config write is detected and pushed live.
"""

from __future__ import annotations

from src.backend.monitoring.state import StateReader
from src.backend.storage.documents import PROGENITOR_STATE_PREFIX, InMemoryDocumentStore


def _reader(servers: dict, mcp_tools: dict | None = None):
    store = InMemoryDocumentStore()
    store.save_document(PROGENITOR_STATE_PREFIX, "mcp", {"mcpServers": servers})
    if mcp_tools is not None:
        store.save_document(PROGENITOR_STATE_PREFIX, "mcp_tools", mcp_tools)
    return StateReader(
        document_store=store,
    ), store


def _mcp_widget(reader: StateReader, name: str) -> dict:
    return next(w for w in reader.get_widgets() if w["type"] == "mcp" and w["name"] == name)


def test_failed_server_surfaces_error_status_and_activity():
    reader, _store = _reader(
        servers={"github": {"url": "https://api.githubcopilot.com/mcp/", "transport": "http"}},
        mcp_tools={
            "tools": [],
            "servers": {
                "github": {
                    "status": "error",
                    "error": "401 Unauthorized",
                    "tool_count": 0,
                    "checked_at": "2026-06-23T00:00:00Z",
                }
            },
        },
    )

    widget = _mcp_widget(reader, "github")
    assert widget["status"] == "error"
    assert widget["status_message"] == "401 Unauthorized"
    assert any(
        "Discovery failed: 401 Unauthorized" in log.get("message", "") for log in widget["logs"]
    )


def test_healthy_server_is_not_marked_error():
    reader, _store = _reader(
        servers={"jina": {"url": "https://mcp.jina.ai/v1", "transport": "http"}},
        mcp_tools={
            "tools": [{"name": "jina_search", "server": "jina", "enabled": True}],
            "servers": {"jina": {"status": "connected", "error": None, "tool_count": 1}},
        },
    )

    widget = _mcp_widget(reader, "jina")
    assert widget["status"] != "error"
    assert widget["status_message"] is None


def test_fingerprint_changes_when_discovered_tools_change():
    reader, store = _reader(
        servers={"jina": {"url": "https://mcp.jina.ai/v1", "transport": "http"}},
        mcp_tools={"tools": []},
    )

    before = reader._mcp_fingerprint()

    # Discovery completes after the config write: tools land in the mcp_tools doc
    # with no change to the server config. The fingerprint must still move.
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "mcp_tools",
        {"tools": [{"name": "jina_search", "server": "jina", "enabled": True}]},
    )

    assert reader._mcp_fingerprint() != before
