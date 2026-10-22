"""Config capabilities."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from src.progenitor.agent.filesystem.base import StateFile, _simple_state_file
from src.progenitor.documents.values import JsonDoc
from src.progenitor.knowledge.indexer_config import (
    default_indexer_payload,
    validate_indexer_document,
)
from src.progenitor.knowledge.schema import validate_knowledge_document
from src.progenitor.mcp.validation import _validate_mcp_tools_document, validate_mcp_document
from src.progenitor.plugins.marketplaces import validate_marketplaces_document
from src.progenitor.plugins.schema import validate_plugins_document
from src.progenitor.registry.validation import validate_registry_document
from src.progenitor.settings.model_profiles import (
    default_model_profiles_payload,
    validate_model_profiles_document,
)
from src.progenitor.settings.preferences import (
    default_preferences_payload,
    validate_preferences_document,
)


def create_mcp_state_file(
    state_manager: Any,
    *,
    on_change: Callable[[], Awaitable[None]] | None = None,
) -> StateFile:
    """Build the ``mcp.json`` state file backed by the StateManager."""
    return _simple_state_file(
        "mcp.json",
        get=state_manager.get_mcp_config,
        save=state_manager.save_mcp_config,
        validate=validate_mcp_document,
        default={"mcpServers": {}},
        on_change=on_change,
    )


def create_mcp_tools_state_file(state_manager: Any) -> StateFile:
    """Build ``mcp_tools.json``: discovered MCP tools, toggleable one by one.

    The tool list is produced by MCP discovery (servers, names, descriptions
    are rewritten on every re-discovery), so the only edit accepted is a
    tool's ``enabled`` flag - the same flag the Tools page toggles. Any other
    change is rejected with an explanation. The runtime's store listener
    re-applies the flags on the change, so a disabled tool disappears from the
    agent's tool set on its next turn; discovery keeps the flag afterwards.
    """

    async def save(doc: JsonDoc) -> None:
        current = await state_manager.get_mcp_tools()
        current_tools = current.get("tools", []) if isinstance(current, dict) else []
        current_by_name = {
            str(tool["name"]): tool
            for tool in current_tools
            if isinstance(tool, dict) and tool.get("name")
        }
        if not isinstance(doc, dict):
            raise ValueError("expected a JSON object")
        # Everything outside `tools` (servers, updated_at) is discovery-owned;
        # an added key here would be silently dropped, so the edit would look
        # successful while changing nothing.
        current_rest = {k: v for k, v in current.items() if k != "tools"}
        edited_rest = {k: v for k, v in doc.items() if k != "tools"}
        if edited_rest != current_rest:
            raise ValueError(
                "only a tool's `enabled` flag inside `tools` can change - there are no "
                "other settings in this file; to disable a tool set "
                '`"enabled": false` on its entry in `tools`'
            )
        new_by_name = {str(tool["name"]): tool for tool in doc.get("tools", [])}
        if set(new_by_name) != set(current_by_name):
            raise ValueError(
                "tools can't be added or removed here - the list comes from MCP discovery; "
                "manage servers in /config/mcp.json and only flip a tool's `enabled` flag here"
            )
        for name, tool in new_by_name.items():
            existing = {k: v for k, v in current_by_name[name].items() if k != "enabled"}
            edited = {k: v for k, v in tool.items() if k != "enabled"}
            if edited != existing:
                raise ValueError(
                    f"tool '{name}': only `enabled` can be changed - its other fields come "
                    "from MCP discovery"
                )
        await state_manager.save_mcp_tools(
            {
                **current,
                "tools": [
                    {**tool, "enabled": bool(new_by_name[str(tool["name"])].get("enabled", True))}
                    for tool in current_tools
                    if isinstance(tool, dict) and tool.get("name")
                ],
            }
        )

    return _simple_state_file(
        "mcp_tools.json",
        get=state_manager.get_mcp_tools,
        save=save,
        validate=_validate_mcp_tools_document,
        default={"tools": []},
    )


def create_plugins_state_file(
    state_manager: Any,
    *,
    on_change: Callable[[], Awaitable[None]] | None = None,
) -> StateFile:
    """Build the ``plugins.json`` state file backed by the StateManager."""
    return _simple_state_file(
        "plugins.json",
        get=state_manager.get_plugins_config,
        save=state_manager.save_plugins_config,
        validate=validate_plugins_document,
        default={"plugins": {}},
        on_change=on_change,
    )


def create_marketplaces_state_file(
    state_manager: Any,
    *,
    on_change: Callable[[], Awaitable[None]] | None = None,
) -> StateFile:
    """Build the ``marketplaces.json`` state file backed by the StateManager."""
    return _simple_state_file(
        "marketplaces.json",
        get=state_manager.get_marketplaces_config,
        save=state_manager.save_marketplaces_config,
        validate=validate_marketplaces_document,
        default={"marketplaces": {}},
        on_change=on_change,
    )


def create_registry_state_file(
    state_manager: Any,
    *,
    on_change: Callable[[], Awaitable[None]] | None = None,
) -> StateFile:
    """Build the ``agents.json`` state file backed by the StateManager.

    Backs the ``registry`` document (``{"agents": [...], "tools": [...]}``);
    named ``agents.json`` to match the backend's document store key
    ``("progenitor", "state", "registry")``.
    """

    async def save(doc: JsonDoc) -> None:
        if not isinstance(doc, dict):
            raise ValueError("agents.json must be a JSON object")
        await state_manager.save_registry(doc)

    return _simple_state_file(
        "agents.json",
        get=state_manager.get_registry,
        save=save,
        validate=validate_registry_document,
        default={"agents": [], "tools": []},
        on_change=on_change,
    )


def _reject_repo_scan_write(_content: Any) -> JsonDoc:
    raise ValueError(
        "repo_scan.json is read-only - it's written by the heartbeat's repo-scan tick, not the agent"
    )


def create_repo_scan_state_file(state_manager: Any) -> StateFile:
    """Build the read-only ``repo_scan.json`` state file backed by the StateManager.

    Surfaces the heartbeat's last derived git state per declared repo (branch,
    dirty count, commit/branch counts, latest commit) so the agent can answer
    "what's the status of X" without shelling out - the on-disk data can lag the
    real repo by up to ``repo_scan_seconds``. Read-only: the document is entirely
    heartbeat-owned, so both ``save`` and ``validate`` reject with the same
    explanatory error rather than silently no-op'ing.
    """

    async def save(_doc: JsonDoc) -> None:
        _reject_repo_scan_write(_doc)

    return _simple_state_file(
        "repo_scan.json",
        get=state_manager.get_repo_scan,
        save=save,
        validate=_reject_repo_scan_write,
        default={"scanned_at": None, "repos": {}, "workspace_signal": {}},
    )


def create_knowledge_state_file(
    state_manager: Any,
    *,
    on_change: Callable[[], Awaitable[None]] | None = None,
) -> StateFile:
    """Build the ``knowledge.json`` state file backed by the StateManager.

    Editing it is the supported way for the agent to manage knowledge-base
    settings; the documents/vectors themselves live in SQL and are ingested via
    the UI. Every write is validated and persists through the normal store path,
    so the Knowledge page updates live via the LISTEN/NOTIFY pipeline.
    """
    return _simple_state_file(
        "knowledge.json",
        get=state_manager.get_knowledge_config,
        save=state_manager.save_knowledge_config,
        validate=validate_knowledge_document,
        default={"bases": []},
        on_change=on_change,
    )


def create_indexer_state_file(state_manager: Any) -> StateFile:
    """Build ``/config/indexer.json``: which model profile embeds knowledge pages.

    ``profile`` must name a ``/config/profile.json`` entry (checked on save);
    progress is reported separately in the read-only ``/knowledge/indexer.json``.
    """
    return _simple_state_file(
        "indexer.json",
        get=state_manager.get_indexer_config,
        save=state_manager.save_indexer_config,
        validate=validate_indexer_document,
        default=default_indexer_payload(),
    )


def create_preferences_state_file(
    state_manager: Any,
    *,
    on_change: Callable[[], Awaitable[None]] | None = None,
) -> StateFile:
    """Build the ``preferences.json`` state file backed by the StateManager.

    Lets the agent read/update the user's profile (display name, timezone) the
    same way it manages knowledge/MCP settings. Every write is validated and
    persists through the normal store path, so the Settings page updates live
    via the LISTEN/NOTIFY pipeline.
    """
    return _simple_state_file(
        "preferences.json",
        get=state_manager.get_preferences_config,
        save=state_manager.save_preferences_config,
        validate=validate_preferences_document,
        default=default_preferences_payload(),
        on_change=on_change,
    )


def create_model_profiles_state_file(state_manager: Any) -> StateFile:
    """Build the provider/deployment map exposed as ``/config/profiles.json``."""
    return _simple_state_file(
        "profiles.json",
        get=state_manager.get_model_profiles_config,
        save=state_manager.save_model_profiles_config,
        validate=validate_model_profiles_document,
        default=default_model_profiles_payload(),
    )


def _reject_health_write(_content: Any) -> JsonDoc:
    raise ValueError("health.json is read-only - it's written by the heartbeat loop, not the agent")


def create_health_state_file(state_manager: Any) -> StateFile:
    """Build the read-only ``health.json`` state file backed by the StateManager.

    Surfaces the heartbeat loop's own system status (status, cycle_count,
    thread_id, pid, git commit ``version``, ``started_at``, LLM model/reasoning
    config) for self-inspection - the `checking-status` skill reads this to
    answer "are you running / what version / what model" questions. Read-only:
    the document is entirely runtime-owned, so both ``save`` and ``validate``
    reject with the same explanatory error rather than silently no-op'ing.
    """

    async def save(_doc: JsonDoc) -> None:
        _reject_health_write(_doc)

    return _simple_state_file(
        "health.json",
        get=state_manager.get_health,
        save=save,
        validate=_reject_health_write,
        default={},
    )
