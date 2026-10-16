"""High-level state manager facade for Progenitor persistence (runtime-store-only)."""

import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from ..content_documents import content_document_key, content_document_prefix
from ..document_kinds import (
    ALERT_KEY_PREFIX,
    DOCUMENT_TYPE_KEY_PREFIX,
    INBOX_MESSAGE_KEY_PREFIX,
    REPO_KEY_PREFIX,
    SESSION_KEY_PREFIX,
    WORKFLOW_APPROVAL_REQUEST_KEY_PREFIX,
)
from ..document_types import DocumentType, builtin_document_type_rows, parse_document_types
from ..routines.store import ROUTINE_GRAPH_KEY_PREFIX, routine_graph_key
from ..utils import utcnow_isoformat
from ..workflows import (
    WORKFLOW_DEFINITION_KEY_PREFIX,
    WORKFLOW_RUN_KEY_PREFIX,
    workflow_definition_key,
    workflow_invoke_request_key,
)
from . import (
    a2a_store,
    acp_store,
    async_subagent_store,
    background_store,
)
from .langgraph_store import LangGraphRuntimeStore
from .model_profiles import default_model_profiles_payload
from .models import (
    A2ATaskRecord,
    ACPTaskRecord,
    AsyncSubagentTaskRecord,
    BackgroundTaskEntry,
    ConversationMessage,
    ScheduleEntry,
    ScheduleRunRecord,
)
from .preferences import default_preferences_payload

logger = logging.getLogger(__name__)


def _structured_log_message(log_name: str, payload: dict[str, Any]) -> str:
    """Extract a concise searchable message from one structured log payload."""
    for field in ("content", "message", "event", "id"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"{log_name} event"


class StateManager:
    """Manages persistence of Progenitor state via the runtime store.

    All state documents live in the shared ``LangGraphRuntimeStore`` (Postgres).
    The manager provides a cached facade over the store with per-key merge
    serialization for concurrent tool calls within the same turn.
    """

    CORE_FILES: dict[str, str] = {
        "current_state": "current_state.json",
        "a2a_tasks": "a2a_tasks.json",
        "acp_tasks": "acp_tasks.json",
        "async_subagent_tasks": "async_subagent_tasks.json",
        "background_tasks": "background_tasks.json",
        "registry": "agents.json",
    }

    # Documents the backend also writes directly to Postgres (repo "Fetch",
    # routine "Run" enqueues). They are never cached, so this process needs no
    # change notifications to stay coherent and a flush on close/commit can
    # never overwrite another writer's update. ``repo:<id>`` documents are
    # likewise always read from and written straight to the store.
    READ_THROUGH_DOCUMENTS: frozenset[str] = frozenset({"repo_scan", "background_tasks"})

    def __init__(
        self,
        runtime_store: LangGraphRuntimeStore,
        database_url: str | None = None,
    ) -> None:
        self._state_cache: dict[str, Any] = {}
        self._runtime_store = runtime_store
        self._database_url = database_url
        # Per-thread-id micro-locks for atomic read-modify-write merges of
        # the *same* thread's bookkeeping document (never held across LLM
        # calls, never contended across different threads).
        self._current_state_merge_locks: dict[str, asyncio.Lock] = {}
        self._initialized = False

    @staticmethod
    def _current_state_document_key(thread_id: str | None) -> str:
        return f"current_state:{thread_id}" if thread_id is not None else "current_state"

    def _merge_lock(self, thread_id: str | None) -> asyncio.Lock:
        key = thread_id or "__global__"
        if key not in self._current_state_merge_locks:
            self._current_state_merge_locks[key] = asyncio.Lock()
        return self._current_state_merge_locks[key]

    async def close(self) -> None:
        """CQ-010: Clean up resources and flush pending writes."""
        if not self._initialized:
            return

        # Flush any cached state to the active store
        try:
            for name in self.CORE_FILES:
                if name in self._state_cache:
                    await self._runtime_store.save_document(name, self._state_cache[name])
            logger.debug("StateManager: flushed cached state on close")
        except Exception as e:
            logger.warning("StateManager: error flushing state on close: %s", e)

        await self._runtime_store.close()
        self._state_cache.clear()
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the runtime store and ensure initial state documents."""
        if self._initialized:
            return

        await self._runtime_store.open()
        await self._ensure_runtime_store_initial_state()
        self._initialized = True

    async def _ensure_runtime_store_initial_state(self) -> None:
        """Ensure all state documents exist in the native runtime store."""
        from ..config import (
            default_marketplaces_config_payload,
            default_mcp_config_payload,
            default_plugins_config_payload,
            default_schedule_config_payload,
        )

        conversation_thread_id = utcnow_isoformat()
        defaults = {
            "current_state": {
                "thread_id": conversation_thread_id,
                "cycle_count": 0,
                "actions_this_cycle": 0,
                "shutdown_requested": False,
                "last_checkpoint": None,
                "messages": [],
                "attachments": [],
                "tool_calls": [],
                "current_input": None,
                "pending_response": None,
            },
            "a2a_tasks": [],
            "acp_tasks": [],
            "async_subagent_tasks": [],
            "background_tasks": [],
            "schedule_runs": [],
            "registry": {},
            "mcp": default_mcp_config_payload(),
            "plugins": default_plugins_config_payload(),
            "marketplaces": default_marketplaces_config_payload(),
            "knowledge": {"bases": []},
            "preferences": default_preferences_payload(),
            "profile": default_model_profiles_payload(),
            "mcp_tools": {"tools": []},
            "schedule": default_schedule_config_payload(),
            "health": {},
        }
        for name, default in defaults.items():
            payload = await self._runtime_store.get_document(name, default)
            if name == "schedule":
                payload, migrated = self._normalize_schedule_payload(payload)
                if migrated:
                    await self._runtime_store.save_document(name, payload)
            if name not in self.READ_THROUGH_DOCUMENTS:
                self._state_cache[name] = payload
        await self._seed_document_types()

    async def _get_runtime_document(self, name: str) -> Any:
        """Return a native-store-backed state document.

        Documents other processes also write (``READ_THROUGH_DOCUMENTS``) are
        always read from the store; the rest are cached.
        """
        if name in self.READ_THROUGH_DOCUMENTS:
            return await self._runtime_store.get_document(name, {})
        if name not in self._state_cache:
            self._state_cache[name] = await self._runtime_store.get_document(name, {})
        return self._state_cache[name]

    async def _save_runtime_document(self, name: str, payload: Any) -> None:
        """Save a native-store-backed state document (caching it unless read-through)."""
        if name not in self.READ_THROUGH_DOCUMENTS:
            self._state_cache[name] = payload
        await self._runtime_store.save_document(name, payload)

    async def save_routine_run_event(self, payload: dict[str, Any]) -> None:
        """Persist lifecycle transitions without retaining them in the state cache."""
        from ..routines.events import RUN_EVENT_PREFIX

        key = f"{RUN_EVENT_PREFIX}{payload['request_id']}:{payload['status']}"
        await self._runtime_store.save_document(key, payload)

    async def get_routine_approval_decision(self, thread_id: str, interrupt_id: str) -> Any:
        from ..routines.approvals import decision_key

        return await self._runtime_store.peek_document(decision_key(thread_id, interrupt_id))

    async def _mutate_runtime_current_state(
        self,
        mutate: Callable[[dict[str, Any]], None],
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Serialize native-store read/modify/write updates to current_state."""
        key = self._current_state_document_key(thread_id)
        async with self._merge_lock(thread_id):
            state = await self._get_runtime_document(key)
            current = dict(state) if isinstance(state, dict) else {}
            mutate(current)
            await self._save_runtime_document(key, current)
            return current

    async def log_conversation(self, message: ConversationMessage) -> None:
        """Log a conversation message to the audit trail.

        Messages live exclusively in the LangGraph checkpoint; this method
        only persists an audit copy in the runtime store.
        """
        payload = message.model_dump(mode="json")
        await self._runtime_store.write_log(
            source="conversation",
            message=_structured_log_message("conversation", payload),
            level="info",
            payload=payload,
            timestamp=payload.pop("timestamp", None),
            relative_path="conversation.jsonl",
        )

    async def log_schedule_event(self, event: dict[str, Any]) -> None:
        """Log a schedule event to the runtime store."""
        payload = dict(event)
        await self._runtime_store.write_log(
            source="schedule",
            message=_structured_log_message("schedule", payload),
            level="info",
            payload=payload,
            timestamp=payload.pop("timestamp", None),
            relative_path="schedule.jsonl",
        )

    # ==========================================================================
    # Schedule management (database-backed in production runtime)
    # ==========================================================================
    @staticmethod
    def _normalize_schedule_payload(payload: Any) -> tuple[dict[str, Any], bool]:
        """Normalize the current object-keyed schedule document.

        Older runtime-store documents used a list of serialized schedule
        entries. Keep those entries readable during the shape migration, while
        rejecting malformed data instead of silently dropping scheduled work.
        """
        if isinstance(payload, dict):
            return payload, False
        if not isinstance(payload, list):
            raise ValueError("schedule document must be an object or legacy entry list")

        normalized: dict[str, Any] = {}
        for item in payload:
            if not isinstance(item, dict):
                raise ValueError("legacy schedule entries must be objects")
            entry_id = item.get("id")
            if not isinstance(entry_id, str) or not entry_id.strip():
                raise ValueError("legacy schedule entries must have a non-empty string id")
            if entry_id in normalized:
                raise ValueError(f"duplicate legacy schedule id: {entry_id}")
            normalized[entry_id] = item
        return normalized, True

    async def get_schedule(self) -> dict[str, ScheduleEntry]:
        """Return the schedule document, migrating the legacy list shape."""
        payload = await self._runtime_store.get_document("schedule", {})
        normalized, migrated = self._normalize_schedule_payload(payload)
        if migrated:
            await self._runtime_store.save_document("schedule", normalized)
        self._state_cache["schedule"] = normalized
        return {k: ScheduleEntry.model_validate(v) for k, v in normalized.items()}

    async def save_schedule(self, schedule: dict[str, ScheduleEntry]) -> None:
        """Persist the schedule document."""
        payload = {k: v.model_dump(mode="json") for k, v in schedule.items()}
        self._state_cache["schedule"] = payload
        await self._runtime_store.save_document("schedule", payload)

    async def update_schedule_entry(self, schedule_id: str, updates: dict[str, Any]) -> bool:
        """Apply partial updates to one schedule entry and persist the document."""
        schedule = await self.get_schedule()
        entry = schedule.get(schedule_id)
        if entry is None:
            return False

        payload = entry.model_dump(mode="json")
        payload.update(updates)
        schedule[schedule_id] = ScheduleEntry.model_validate(payload)
        await self.save_schedule(schedule)
        return True

    # ==========================================================================
    # Health management (heartbeat-loop system status)
    # ==========================================================================
    async def save_health(self, payload: dict[str, Any]) -> None:
        """Persist the health document."""
        await self._save_runtime_document("health", payload)

    async def get_health(self) -> dict[str, Any]:
        """Return the heartbeat-loop health document."""
        health = await self._runtime_store.get_document("health", {})
        self._state_cache["health"] = health
        return dict(health) if isinstance(health, dict) else {}

    # ==========================================================================
    # Registry management
    # ==========================================================================
    async def get_registry(self) -> dict[str, Any]:
        """Return the tool registry document."""
        registry = await self._get_runtime_document("registry")
        return dict(registry) if isinstance(registry, dict) else {}

    async def save_registry(self, registry: dict[str, Any]) -> None:
        """Persist the tool registry document."""
        await self._save_runtime_document("registry", registry)

    # ==========================================================================
    # Repos (declared repositories, one ``repo:<id>`` document each)
    # ==========================================================================
    async def get_repo(self, repo_id: str) -> dict[str, Any] | None:
        """Return one declared repo entry, or ``None`` if it isn't declared."""
        payload = await self._runtime_store.peek_document(f"{REPO_KEY_PREFIX}{repo_id}")
        return dict(payload) if isinstance(payload, dict) else None

    async def list_repos(self) -> dict[str, dict[str, Any]]:
        """Return every declared repo entry, keyed by repo id."""
        rows = await self._runtime_store.list_documents(REPO_KEY_PREFIX)
        return {
            key.removeprefix(REPO_KEY_PREFIX): dict(payload)
            for key, payload in rows
            if isinstance(payload, dict)
        }

    async def save_repo(self, repo_id: str, entry: dict[str, Any]) -> None:
        """Persist one declared repo entry (uncached; the backend writes these too)."""
        await self._runtime_store.save_document(f"{REPO_KEY_PREFIX}{repo_id}", entry)

    async def delete_repo(self, repo_id: str) -> None:
        """Remove one declared repo entry."""
        await self._runtime_store.delete_document(f"{REPO_KEY_PREFIX}{repo_id}")

    # ==========================================================================
    # Repo scan (heartbeat-derived git state, read-only for the agent)
    # ==========================================================================
    async def get_repo_scan(self) -> dict[str, Any]:
        """Return the heartbeat's last derived git-state scan."""
        scan = await self._get_runtime_document("repo_scan")
        return dict(scan) if isinstance(scan, dict) else {}

    async def save_repo_scan(self, payload: dict[str, Any]) -> None:
        """Persist the heartbeat's derived git-state scan."""
        await self._save_runtime_document("repo_scan", payload)

    # ==========================================================================
    # MCP configuration
    # ==========================================================================
    async def get_mcp_config(self) -> dict[str, Any]:
        """Return the MCP configuration document.

        Read straight from the shared store rather than the in-memory
        cache: the MCP config document is also created/edited by the
        backend (UI) writing the same Postgres document, so a cached copy
        would hide those cross-process changes from the runtime's periodic
        MCP refresh. See get_schedule for the same rationale.
        """
        from ..config import default_mcp_config_payload
        from ..mcp.loader import normalize_mcp_config_payload

        payload = await self._runtime_store.get_document("mcp", default_mcp_config_payload())
        self._state_cache["mcp"] = payload
        return normalize_mcp_config_payload(
            payload if isinstance(payload, dict) else default_mcp_config_payload()
        )

    async def save_mcp_config(self, payload: dict[str, Any]) -> None:
        """Persist the MCP configuration document."""
        from ..mcp.loader import normalize_mcp_config_payload

        normalized = normalize_mcp_config_payload(payload)
        await self._save_runtime_document("mcp", normalized)

    # ==========================================================================
    # Plugins configuration
    # ==========================================================================
    async def get_plugins_config(self) -> dict[str, Any]:
        """Return the plugins configuration document.

        Read straight from the shared store rather than the in-memory
        cache: the plugins config document is also created/edited by the
        reconciler and the backend, so a cached copy would hide those
        cross-process changes.
        """
        from ..config import default_plugins_config_payload

        payload = await self._runtime_store.get_document(
            "plugins", default_plugins_config_payload()
        )
        self._state_cache["plugins"] = payload
        return payload if isinstance(payload, dict) else default_plugins_config_payload()

    async def save_plugins_config(self, payload: dict[str, Any]) -> None:
        """Persist the plugins configuration document.

        Validates the payload against the plugin entry schema before saving,
        raising ValueError on invalid input so a violating edit is rejected
        and nothing changes.
        """
        from ..agent.state_backend import validate_plugins_document

        validate_plugins_document(json.dumps(payload))
        await self._save_runtime_document("plugins", payload)

    # ==========================================================================
    # Marketplaces configuration
    # ==========================================================================
    async def get_marketplaces_config(self) -> dict[str, Any]:
        """Return the marketplaces configuration document.

        Read straight from the shared store rather than the in-memory
        cache: the backend also reads/writes this same Postgres document
        directly, so a cached copy would hide those cross-process changes.
        """
        from ..config import default_marketplaces_config_payload

        payload = await self._runtime_store.get_document(
            "marketplaces", default_marketplaces_config_payload()
        )
        self._state_cache["marketplaces"] = payload
        return payload if isinstance(payload, dict) else default_marketplaces_config_payload()

    async def save_marketplaces_config(self, payload: dict[str, Any]) -> None:
        """Persist the marketplaces configuration document.

        Validates the payload against the marketplace entry schema before
        saving, raising ValueError on invalid input so a violating edit is
        rejected and nothing changes.
        """
        from ..agent.state_backend import validate_marketplaces_document

        validate_marketplaces_document(json.dumps(payload))
        await self._save_runtime_document("marketplaces", payload)

    # ==========================================================================
    # Knowledge configuration
    # ==========================================================================
    async def get_knowledge_config(self) -> dict[str, Any]:
        """Return the knowledge document from the shared runtime store.

        The backend edits this same Postgres document, so read directly from the
        store rather than the in-memory cache to preserve cross-process changes.
        """
        default: dict[str, Any] = {"bases": []}
        payload = await self._runtime_store.get_document("knowledge", default)
        return payload if isinstance(payload, dict) else dict(default)

    async def save_knowledge_config(self, payload: dict[str, Any]) -> None:
        """Persist the knowledge document to the shared runtime store."""
        await self._save_runtime_document("knowledge", payload)

    # ==========================================================================
    # User preferences (profile)
    # ==========================================================================
    async def get_preferences_config(self) -> dict[str, Any]:
        """Return the preferences document from the shared runtime store.

        The backend edits this same Postgres document (Settings page), so read
        directly from the store rather than the in-memory cache to preserve
        cross-process changes.
        """
        default = default_preferences_payload()
        payload = await self._runtime_store.get_document("preferences", default)
        return payload if isinstance(payload, dict) else dict(default)

    async def save_preferences_config(self, payload: dict[str, Any]) -> None:
        """Persist the preferences document to the shared runtime store."""
        await self._save_runtime_document("preferences", payload)

    # ==========================================================================
    # Model profiles
    # ==========================================================================
    async def get_model_profiles_config(self) -> dict[str, Any]:
        """Return the shared model profile map behind ``/config/profiles.json``."""
        default = default_model_profiles_payload()
        payload = await self._runtime_store.get_document("profile", default)
        return payload if isinstance(payload, dict) else default

    async def save_model_profiles_config(self, payload: dict[str, Any]) -> None:
        """Persist the validated shared model profile map."""
        await self._save_runtime_document("profile", payload)

    # ==========================================================================
    # Kanban board
    # ==========================================================================
    async def get_kanban_structure(self) -> dict[str, Any]:
        """Return the kanban board structure document (columns + ``card_order``).

        Read straight from the shared store (not the cache): the backend (UI)
        edits this same document, so a cached copy would hide cross-process
        changes. See get_mcp_config for the same rationale.
        """
        default: dict[str, Any] = {
            "id": "progenitor-kanban",
            "columns": [],
            "card_order": [],
        }
        payload = await self._runtime_store.get_document("kanban_board", default)
        return payload if isinstance(payload, dict) else dict(default)

    async def save_kanban_structure(self, structure: dict[str, Any]) -> None:
        """Persist the kanban board structure document."""
        await self._save_runtime_document("kanban_board", structure)

    @staticmethod
    def _kanban_card_key(card_id: str) -> str:
        return f"kanban_card:{card_id}"

    async def list_kanban_card_ids(self) -> list[str]:
        """Return the ordered list of card ids from the structure document."""
        structure = await self.get_kanban_structure()
        order = structure.get("card_order")
        if not isinstance(order, list):
            return []
        return [str(cid).strip() for cid in order if str(cid).strip()]

    async def get_kanban_card(self, card_id: str) -> dict[str, Any] | None:
        """Return one card document, or ``None`` if it does not exist."""
        key = self._kanban_card_key(card_id)
        payload = await self._runtime_store.get_document(key, None)
        return payload if isinstance(payload, dict) else None

    async def save_kanban_card(self, card_id: str, card: dict[str, Any]) -> None:
        """Persist one card document and ensure it is tracked in ``card_order``."""
        key = self._kanban_card_key(card_id)
        await self._save_runtime_document(key, card)
        structure = await self.get_kanban_structure()
        order = structure.get("card_order")
        order = (
            [str(cid).strip() for cid in order if str(cid).strip()]
            if isinstance(order, list)
            else []
        )
        if card_id not in order:
            order.append(card_id)
            structure["card_order"] = order
            await self.save_kanban_structure(structure)

    async def delete_kanban_card(self, card_id: str) -> None:
        """Remove one card document and drop it from ``card_order``."""
        key = self._kanban_card_key(card_id)
        await self._runtime_store.delete_document(key)
        structure = await self.get_kanban_structure()
        order = structure.get("card_order")
        if isinstance(order, list) and card_id in order:
            structure["card_order"] = [cid for cid in order if cid != card_id]
            await self.save_kanban_structure(structure)

    async def get_kanban_board(self) -> dict[str, Any]:
        """Assemble the full board (structure + per-card documents).

        Tolerant of a legacy structure that still carries an inline ``cards``
        array (the backend migrates those to per-card rows on its next read).
        """
        structure = await self.get_kanban_structure()
        inline = structure.get("cards")
        if isinstance(inline, list) and inline and "card_order" not in structure:
            cards = [card for card in inline if isinstance(card, dict)]
        else:
            cards = []
            for card_id in await self.list_kanban_card_ids():
                card = await self.get_kanban_card(card_id)
                if card is not None:
                    cards.append(card)
        assembled = {k: v for k, v in structure.items() if k not in ("cards", "card_order")}
        assembled["cards"] = cards
        return assembled

    # ==========================================================================
    # Inbox messages
    # ==========================================================================
    @staticmethod
    def _inbox_message_key(message_id: str) -> str:
        return f"{INBOX_MESSAGE_KEY_PREFIX}{message_id}"

    async def get_inbox_message(self, message_id: str) -> dict[str, Any] | None:
        """Return one inbox message document, or ``None`` if it does not exist.

        Reads straight from the shared store (never the cache, and never
        creating a default row): the backend mutates these same rows
        (mark-read/delete), and the message list is a key-prefix scan a junk
        row would pollute.
        """
        key = self._inbox_message_key(message_id)
        payload = await self._runtime_store.peek_document(key)
        return payload if isinstance(payload, dict) else None

    async def save_inbox_message(self, message_id: str, message: dict[str, Any]) -> None:
        """Persist one inbox message document (no index to maintain)."""
        key = self._inbox_message_key(message_id)
        await self._save_runtime_document(key, message)

    async def delete_inbox_message(self, message_id: str) -> None:
        """Remove one inbox message document."""
        key = self._inbox_message_key(message_id)
        await self._runtime_store.delete_document(key)

    async def list_inbox_messages(self) -> list[dict[str, Any]]:
        """Return all inbox message documents (unsorted; callers order them)."""
        rows = await self._runtime_store.list_documents(INBOX_MESSAGE_KEY_PREFIX)
        return [payload for _, payload in rows if isinstance(payload, dict)]

    # ==========================================================================
    # Session documents
    # ==========================================================================
    async def get_session(self, thread_id: str) -> dict[str, Any] | None:
        """Return ``session:<thread_id>``'s payload, or ``None`` if the thread has none."""
        return await self._runtime_store.peek_document(f"{SESSION_KEY_PREFIX}{thread_id}")

    async def list_sessions(self) -> list[dict[str, Any]]:
        """Return every session document payload (unsorted)."""
        return [
            payload for _, payload in await self._runtime_store.list_documents(SESSION_KEY_PREFIX)
        ]

    async def merge_session(self, thread_id: str, fields: dict[str, Any]) -> bool:
        """Atomically merge ``fields`` into ``session:<thread_id>`` payload.

        Returns ``True`` when the row existed.
        """
        return await self._runtime_store.merge_session(thread_id, fields)

    # ==========================================================================
    # Workflow definitions
    # ==========================================================================
    async def get_workflow_definition(self, workflow_id: str) -> dict[str, Any] | None:
        """Return one workflow definition document, or ``None`` if missing."""
        key = workflow_definition_key(workflow_id)
        payload = await self._runtime_store.peek_document(key)
        return payload if isinstance(payload, dict) else None

    async def save_workflow_definition(self, workflow_id: str, payload: dict[str, Any]) -> None:
        """Persist one workflow definition document."""
        key = workflow_definition_key(workflow_id)
        await self._save_runtime_document(key, payload)

    async def delete_workflow_definition(self, workflow_id: str) -> None:
        """Remove one workflow definition document."""
        key = workflow_definition_key(workflow_id)
        await self._runtime_store.delete_document(key)

    async def list_workflow_definitions(self) -> list[dict[str, Any]]:
        """Return all workflow definition documents."""
        rows = await self._runtime_store.list_documents(WORKFLOW_DEFINITION_KEY_PREFIX)
        return [payload for _, payload in rows if isinstance(payload, dict)]

    # ==========================================================================
    # Document types
    # ==========================================================================
    async def list_document_types(self) -> dict[str, DocumentType]:
        """Return the ``document_type:<id>`` registry, keyed by type id."""
        rows = await self._runtime_store.list_documents(DOCUMENT_TYPE_KEY_PREFIX)
        return parse_document_types(payload for _, payload in rows)

    async def _seed_document_types(self) -> None:
        """Write the built-in document types, touching only rows that differ."""
        for key, payload in builtin_document_type_rows():
            if await self._runtime_store.peek_document(key) != payload:
                await self._runtime_store.save_document(key, payload)

    # ==========================================================================
    # Routine graphs
    # ==========================================================================
    async def get_execution_policy(self, policy_id: str) -> dict[str, Any] | None:
        from ..policies import POLICY_KEY_PREFIX

        payload = await self._runtime_store.peek_document(f"{POLICY_KEY_PREFIX}{policy_id}")
        return payload if isinstance(payload, dict) else None

    async def save_execution_policy(self, policy_id: str, payload: dict[str, Any]) -> None:
        from ..policies import POLICY_KEY_PREFIX, validate_policy

        policy = validate_policy(payload)
        if policy.id != policy_id:
            raise ValueError("Policy id must match filename")
        await self._save_runtime_document(
            f"{POLICY_KEY_PREFIX}{policy_id}", policy.model_dump(mode="json")
        )

    async def delete_execution_policy(self, policy_id: str) -> None:
        from ..policies import POLICY_KEY_PREFIX

        await self._runtime_store.delete_document(f"{POLICY_KEY_PREFIX}{policy_id}")

    async def list_execution_policies(self) -> list[dict[str, Any]]:
        from ..policies import POLICY_KEY_PREFIX

        rows = await self._runtime_store.list_documents(POLICY_KEY_PREFIX)
        return [payload for _, payload in rows if isinstance(payload, dict)]

    async def get_routine_graph(self, agent_id: str) -> dict[str, Any] | None:
        """Return one agent's routine graph document, or ``None`` if missing."""
        key = routine_graph_key(agent_id)
        payload = await self._runtime_store.peek_document(key)
        return payload if isinstance(payload, dict) else None

    async def save_routine_graph(self, agent_id: str, payload: dict[str, Any]) -> None:
        """Persist one agent's routine graph document."""
        key = routine_graph_key(agent_id)
        await self._save_runtime_document(key, payload)

    async def delete_routine_graph(self, agent_id: str) -> None:
        """Remove one agent's routine graph document."""
        key = routine_graph_key(agent_id)
        await self._runtime_store.delete_document(key)

    async def list_routine_graphs(self) -> list[dict[str, Any]]:
        """Return all routine graph documents."""
        rows = await self._runtime_store.list_documents(ROUTINE_GRAPH_KEY_PREFIX)
        return [payload for _, payload in rows if isinstance(payload, dict)]

    async def list_workflow_runs(self) -> list[dict[str, Any]]:
        """Return all workflow run documents."""
        rows = await self._runtime_store.list_documents(WORKFLOW_RUN_KEY_PREFIX)
        return [payload for _, payload in rows if isinstance(payload, dict)]

    async def request_workflow_run(
        self,
        *,
        request_id: str,
        workflow_id: str,
        input: dict[str, Any],
        ancestry: list[str],
    ) -> None:
        """Persist a durable request for the backend workflow executor."""
        await self._save_runtime_document(
            workflow_invoke_request_key(request_id),
            {
                "request_id": request_id,
                "workflow_id": workflow_id,
                "input": input,
                "ancestry": ancestry,
                "requested_at": utcnow_isoformat(),
            },
        )

    # ==========================================================================
    # Alerts (one ``alert:<id>`` document each; the backend relays new ones)
    # ==========================================================================
    async def save_alert(self, alert: dict[str, Any]) -> None:
        """Persist one new alert document (see ``app.progenitor.alerts``)."""
        await self._runtime_store.save_document(f"{ALERT_KEY_PREFIX}{alert['id']}", alert)

    async def list_alerts(self) -> list[dict[str, Any]]:
        """Return every alert document, newest first."""
        rows = await self._runtime_store.list_documents(ALERT_KEY_PREFIX)
        alerts = [payload for _, payload in rows if isinstance(payload, dict)]
        return sorted(alerts, key=lambda alert: alert.get("timestamp") or 0, reverse=True)

    async def request_workflow_approval(
        self, *, request_id: str, workflow_id: str, run_id: str, approved: bool
    ) -> None:
        """Persist Progenitor's answer to a workflow approval for the backend to apply."""
        await self._save_runtime_document(
            f"{WORKFLOW_APPROVAL_REQUEST_KEY_PREFIX}{request_id}",
            {
                "request_id": request_id,
                "workflow_id": workflow_id,
                "run_id": run_id,
                "approved": approved,
                "requested_at": utcnow_isoformat(),
            },
        )

    # ==========================================================================
    # Content documents (prompts, skills, runtime memories)
    # ==========================================================================
    async def get_content_document(self, kind: str, relative_path: str) -> str | None:
        """Return one content document's text, or ``None`` if missing/malformed."""
        key = content_document_key(kind, relative_path)
        payload = await self._runtime_store.peek_document(key)
        if isinstance(payload, dict) and isinstance((content := payload.get("content")), str):
            return content
        return None

    async def save_content_document(self, kind: str, relative_path: str, content: str) -> None:
        """Persist one content document's text."""
        key = content_document_key(kind, relative_path)
        await self._save_runtime_document(
            key, {"content": content, "updated_at": utcnow_isoformat()}
        )

    async def delete_content_document(self, kind: str, relative_path: str) -> None:
        """Remove one content document."""
        key = content_document_key(kind, relative_path)
        await self._runtime_store.delete_document(key)

    async def list_content_documents(self, kind: str) -> list[tuple[str, str]]:
        """Return ``(relative_path, content)`` for every document of one kind."""
        prefix = content_document_prefix(kind)
        rows = await self._runtime_store.list_documents(prefix)
        results: list[tuple[str, str]] = []
        for key, payload in rows:
            if isinstance(payload, dict) and isinstance(payload.get("content"), str):
                results.append((key[len(prefix) :], payload["content"]))
        return results

    # ==========================================================================
    # MCP tools
    # ==========================================================================
    async def get_mcp_tools(self) -> dict[str, Any]:
        """Return the runtime-owned discovered MCP tools document.

        Read straight from the shared store (not the cache): the backend (UI)
        toggles per-tool enabled flags by writing this same document, so a cached
        copy would hide those cross-process changes. See get_schedule.
        """
        default: dict[str, Any] = {"tools": []}
        payload = await self._runtime_store.get_document("mcp_tools", default)
        self._state_cache["mcp_tools"] = payload
        return payload if isinstance(payload, dict) else dict(default)

    async def save_mcp_tools(self, payload: dict[str, Any]) -> None:
        """Persist the runtime-owned discovered MCP tools document."""
        self._state_cache["mcp_tools"] = payload
        await self._runtime_store.save_document("mcp_tools", payload)

    # ==========================================================================
    # Current State Management
    # ==========================================================================

    async def get_current_state(self, thread_id: str | None = None) -> dict[str, Any]:
        """Return the current agent operational state document."""
        key = self._current_state_document_key(thread_id)
        state = await self._get_runtime_document(key)
        return dict(state) if isinstance(state, dict) else {}

    async def save_current_state(self, state: dict[str, Any], thread_id: str | None = None) -> None:
        """Persist the current agent operational state document."""
        key = self._current_state_document_key(thread_id)
        await self._save_runtime_document(key, state)

    async def update_current_state(
        self, updates: dict[str, Any], thread_id: str | None = None
    ) -> None:
        """Apply an in-place update to the current state document."""
        await self._mutate_runtime_current_state(
            lambda current: current.update(updates), thread_id=thread_id
        )

    async def delete_current_state(self, thread_id: str) -> None:
        """Remove a conversation's current state document (when it is deleted)."""
        key = self._current_state_document_key(thread_id)
        self._state_cache.pop(key, None)
        await self._runtime_store.delete_document(key)

    # ==========================================================================
    # A2A Task Tracking
    # ==========================================================================

    async def get_a2a_tasks(self) -> list[A2ATaskRecord]:
        """Return all A2A task records."""
        payload = await self._get_runtime_document("a2a_tasks")
        return [A2ATaskRecord.model_validate(item) for item in payload]

    async def save_a2a_tasks(self, tasks: list[A2ATaskRecord]) -> None:
        """Persist all A2A task records."""
        await self._save_runtime_document(
            "a2a_tasks",
            [task.model_dump(mode="json") for task in tasks],
        )

    async def get_active_a2a_tasks(self) -> list[A2ATaskRecord]:
        """Return A2A tasks that are still active (not completed/failed)."""
        return await a2a_store.get_active_a2a_tasks(get_a2a_tasks_fn=self.get_a2a_tasks)

    async def get_a2a_task(self, task_id: str) -> A2ATaskRecord | None:
        """Return one A2A task by id, or ``None`` if not found."""
        return await a2a_store.get_a2a_task(task_id=task_id, get_a2a_tasks_fn=self.get_a2a_tasks)

    async def upsert_a2a_task(self, task: A2ATaskRecord) -> None:
        """Insert or update an A2A task record."""
        await a2a_store.upsert_a2a_task(
            task=task,
            get_a2a_tasks_fn=self.get_a2a_tasks,
            save_a2a_tasks_fn=self.save_a2a_tasks,
        )

    async def update_a2a_task(self, task_id: str, updates: dict[str, Any]) -> bool:
        """Apply partial updates to an A2A task. Returns ``True`` if the task existed."""
        return await a2a_store.update_a2a_task(
            task_id=task_id,
            updates=updates,
            get_a2a_tasks_fn=self.get_a2a_tasks,
            save_a2a_tasks_fn=self.save_a2a_tasks,
        )

    # ==========================================================================
    # ACP Task Tracking
    # ==========================================================================

    async def get_acp_tasks(self) -> list[ACPTaskRecord]:
        """Return all ACP task records."""
        payload = await self._get_runtime_document("acp_tasks")
        return [ACPTaskRecord.model_validate(item) for item in payload]

    async def save_acp_tasks(self, tasks: list[ACPTaskRecord]) -> None:
        """Persist all ACP task records."""
        await self._save_runtime_document(
            "acp_tasks",
            [task.model_dump(mode="json") for task in tasks],
        )

    async def get_acp_task(self, task_id: str) -> ACPTaskRecord | None:
        """Return one ACP task by id, or ``None`` if not found."""
        return await acp_store.get_acp_task(task_id=task_id, get_acp_tasks_fn=self.get_acp_tasks)

    async def upsert_acp_task(self, task: ACPTaskRecord) -> None:
        """Insert or update an ACP task record."""
        await acp_store.upsert_acp_task(
            task=task,
            get_acp_tasks_fn=self.get_acp_tasks,
            save_acp_tasks_fn=self.save_acp_tasks,
        )

    async def update_acp_task(self, task_id: str, updates: dict[str, Any]) -> bool:
        """Apply partial updates to an ACP task. Returns ``True`` if the task existed."""
        return await acp_store.update_acp_task(
            task_id=task_id,
            updates=updates,
            get_acp_tasks_fn=self.get_acp_tasks,
            save_acp_tasks_fn=self.save_acp_tasks,
        )

    # ==========================================================================
    # Async Subagent Task Tracking
    # ==========================================================================

    async def get_async_subagent_tasks(self) -> list[AsyncSubagentTaskRecord]:
        """Return all async subagent task records."""
        payload = await self._get_runtime_document("async_subagent_tasks")
        return [AsyncSubagentTaskRecord.model_validate(item) for item in payload]

    async def save_async_subagent_tasks(self, tasks: list[AsyncSubagentTaskRecord]) -> None:
        """Persist all async subagent task records."""
        await self._save_runtime_document(
            "async_subagent_tasks",
            [task.model_dump(mode="json") for task in tasks],
        )

    async def get_async_subagent_task(self, task_id: str) -> AsyncSubagentTaskRecord | None:
        """Return one async subagent task by id, or ``None`` if not found."""
        return await async_subagent_store.get_async_subagent_task(
            task_id=task_id,
            get_async_subagent_tasks_fn=self.get_async_subagent_tasks,
        )

    async def upsert_async_subagent_task(self, task: AsyncSubagentTaskRecord) -> None:
        """Insert or update an async subagent task record."""
        await async_subagent_store.upsert_async_subagent_task(
            task=task,
            get_async_subagent_tasks_fn=self.get_async_subagent_tasks,
            save_async_subagent_tasks_fn=self.save_async_subagent_tasks,
        )

    async def update_async_subagent_task(self, task_id: str, updates: dict[str, Any]) -> bool:
        """Apply partial updates to an async subagent task. Returns ``True`` if found."""
        return await async_subagent_store.update_async_subagent_task(
            task_id=task_id,
            updates=updates,
            get_async_subagent_tasks_fn=self.get_async_subagent_tasks,
            save_async_subagent_tasks_fn=self.save_async_subagent_tasks,
        )

    # ==========================================================================
    # Background Task Queue
    # ==========================================================================

    async def get_background_tasks(self) -> list[BackgroundTaskEntry]:
        """Return all background task entries."""
        payload = await self._get_runtime_document("background_tasks")
        return [BackgroundTaskEntry.model_validate(item) for item in payload]

    async def save_background_tasks(self, tasks: list[BackgroundTaskEntry]) -> None:
        """Persist all background task entries."""
        await self._save_runtime_document(
            "background_tasks",
            [task.model_dump(mode="json") for task in tasks],
        )

    @asynccontextmanager
    async def _with_background_tasks_lock(self):
        """Acquire a Postgres advisory lock for atomic background_tasks RMW.

        Yields immediately when no database_url is configured (in-memory store).
        """
        if not self._database_url:
            yield
            return
        from psycopg import AsyncConnection

        async with await AsyncConnection.connect(self._database_url, autocommit=True) as conn:
            await conn.execute("SELECT pg_advisory_lock(hashtextextended('background_tasks', 0))")
            try:
                yield
            finally:
                await conn.execute(
                    "SELECT pg_advisory_unlock(hashtextextended('background_tasks', 0))"
                )

    async def enqueue_background_task(self, task: BackgroundTaskEntry) -> None:
        """Add a background task to the queue."""
        async with self._with_background_tasks_lock():
            await background_store.enqueue_background_task(
                task=task,
                get_background_tasks_fn=self.get_background_tasks,
                save_background_tasks_fn=self.save_background_tasks,
            )

    async def dequeue_background_task(self) -> BackgroundTaskEntry | None:
        """Remove and return the next background task, or ``None`` if empty."""
        async with self._with_background_tasks_lock():
            return await background_store.dequeue_background_task(
                get_background_tasks_fn=self.get_background_tasks,
                save_background_tasks_fn=self.save_background_tasks,
            )

    # ==========================================================================
    # Schedule Run Tracking
    # ==========================================================================

    async def get_schedule_runs(self) -> list[ScheduleRunRecord]:
        """Return all schedule run records."""
        payload = await self._get_runtime_document("schedule_runs")
        return [ScheduleRunRecord.model_validate(item) for item in payload]

    async def save_schedule_runs(self, runs: list[ScheduleRunRecord]) -> None:
        """Persist all schedule run records."""
        await self._save_runtime_document(
            "schedule_runs",
            [run.model_dump(mode="json") for run in runs],
        )

    async def get_schedule_run(self, run_id: str) -> ScheduleRunRecord | None:
        """Return one schedule run by id, or ``None`` if not found."""
        for run in await self.get_schedule_runs():
            if run.run_id == run_id:
                return run
        return None

    async def upsert_schedule_run(self, run: ScheduleRunRecord) -> None:
        """Insert or update a schedule run record."""
        runs = await self.get_schedule_runs()
        for index, existing in enumerate(runs):
            if existing.run_id == run.run_id:
                runs[index] = run
                break
        else:
            runs.append(run)
        await self.save_schedule_runs(runs)

    async def update_schedule_run(self, run_id: str, updates: dict[str, Any]) -> bool:
        """Apply partial updates to a schedule run. Returns ``True`` if found."""
        runs = await self.get_schedule_runs()
        for index, existing in enumerate(runs):
            if existing.run_id != run_id:
                continue
            payload = existing.model_dump()
            payload.update(updates)
            runs[index] = ScheduleRunRecord(**payload)
            await self.save_schedule_runs(runs)
            return True
        return False

    # ==========================================================================
    # Messages Management (stored in current_state document)
    # ==========================================================================

    async def commit_state(self) -> None:
        """Commit all cached state to the runtime store."""
        for name, data in self._state_cache.items():
            if name not in self.CORE_FILES:
                continue
            await self._runtime_store.save_document(name, data)
