"""High-level state manager facade for Progena persistence (runtime-store-only)."""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from src.progena.a2a.models import A2ATaskRecord
from src.progena.a2a.store import A2ATaskStore
from src.progena.acp.models import ACPTaskRecord
from src.progena.acp.store import ACPTaskStore
from src.progena.alerts.store import AlertStore
from src.progena.async_subagents.models import AsyncSubagentTaskRecord
from src.progena.async_subagents.store import AsyncSubagentStore
from src.progena.documents.store import DocumentStore
from src.progena.documents.types import DocumentType
from src.progena.inbox.store import InboxStore
from src.progena.knowledge.store import KnowledgeStore
from src.progena.mcp.store import MCPStore
from src.progena.orchestration.state import BackgroundTaskEntry
from src.progena.orchestration.store import BackgroundStore
from src.progena.persistence.documents import StateDocuments
from src.progena.persistence.store import LangGraphRuntimeStore
from src.progena.plugins.store import PluginStore
from src.progena.registry.documents import RegistryStore
from src.progena.repositories.store import RepositoryStore
from src.progena.routines.documents import RoutineStore
from src.progena.schedules.models import ScheduleEntry, ScheduleRunRecord
from src.progena.schedules.store import ScheduleStore
from src.progena.sessions.models import ConversationMessage
from src.progena.sessions.store import SessionStore
from src.progena.settings.model_profiles import default_model_profiles_payload
from src.progena.settings.preferences import default_preferences_payload
from src.progena.settings.store import SettingsStore
from src.progena.tasks.store import TaskStore
from src.progena.workflows.store import WorkflowStore

from ..utils import utcnow_isoformat

logger = logging.getLogger(__name__)


class StateManager:
    """Manages persistence of Progena state via the runtime store.

    All state documents live in the shared ``LangGraphRuntimeStore`` (Postgres).
    The manager provides a cached facade over the store with per-key merge
    serialization for concurrent tool calls within the same turn.
    """

    CORE_FILES: dict[str, str] = {
        "current_state": "current_state.json",
        "a2a_tasks": "a2a_tasks.json",
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
        self._document_access = StateDocuments(
            runtime_store, self._state_cache, database_url, self.READ_THROUGH_DOCUMENTS
        )
        self.settings = SettingsStore(self._document_access)
        self.schedules = ScheduleStore(self._document_access)
        self.repositories = RepositoryStore(self._document_access)
        self.registry = RegistryStore(self._document_access)
        self.mcp = MCPStore(self._document_access)
        self.plugins = PluginStore(self._document_access)
        self.knowledge = KnowledgeStore(
            self._document_access, self.settings.get_model_profiles_config
        )
        self.tasks = TaskStore(self._document_access)
        self.inbox = InboxStore(self._document_access)
        self.sessions = SessionStore(self._document_access)
        self.workflows = WorkflowStore(self._document_access)
        self.routines = RoutineStore(self._document_access)
        self.documents = DocumentStore(self._document_access)
        self.alerts = AlertStore(self._document_access)
        self.a2a = A2ATaskStore(self._document_access)
        self.acp = ACPTaskStore(self._document_access)
        self.async_subagents = AsyncSubagentStore(self._document_access)
        self.background = BackgroundStore(self._document_access)

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
        return await self._document_access.get(name)

    async def _save_runtime_document(self, name: str, payload: Any) -> None:
        await self._document_access.save(name, payload)

    async def save_routine_run_event(self, payload: dict[str, Any]) -> None:
        return await self.routines.save_routine_run_event(payload)

    async def get_routine_approval_decision(self, thread_id: str, interrupt_id: str) -> Any:
        return await self.routines.get_routine_approval_decision(thread_id, interrupt_id)

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
        return await self.sessions.log_conversation(message)

    async def log_schedule_event(self, event: dict[str, Any]) -> None:
        return await self.schedules.log_schedule_event(event)

    # ==========================================================================
    # Schedule management (database-backed in production runtime)
    # ==========================================================================
    @staticmethod
    def _normalize_schedule_payload(payload: Any) -> tuple[dict[str, Any], bool]:
        return ScheduleStore._normalize_schedule_payload(payload)

    async def get_schedule(self) -> dict[str, ScheduleEntry]:
        return await self.schedules.get_schedule()

    async def save_schedule(self, schedule: dict[str, ScheduleEntry]) -> None:
        return await self.schedules.save_schedule(schedule)

    async def update_schedule_entry(self, schedule_id: str, updates: dict[str, Any]) -> bool:
        return await self.schedules.update_schedule_entry(schedule_id, updates)

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
        return await self.registry.get_registry()

    async def save_registry(self, registry: dict[str, Any]) -> None:
        return await self.registry.save_registry(registry)

    # ==========================================================================
    # Repos (declared repositories, one ``repo:<id>`` document each)
    # ==========================================================================
    async def get_repo(self, repo_id: str) -> dict[str, Any] | None:
        return await self.repositories.get_repo(repo_id)

    async def list_repos(self) -> dict[str, dict[str, Any]]:
        return await self.repositories.list_repos()

    async def save_repo(self, repo_id: str, entry: dict[str, Any]) -> None:
        return await self.repositories.save_repo(repo_id, entry)

    async def delete_repo(self, repo_id: str) -> None:
        return await self.repositories.delete_repo(repo_id)

    # ==========================================================================
    # Repo scan (heartbeat-derived git state, read-only for the agent)
    # ==========================================================================
    async def get_repo_scan(self) -> dict[str, Any]:
        return await self.repositories.get_repo_scan()

    async def save_repo_scan(self, payload: dict[str, Any]) -> None:
        return await self.repositories.save_repo_scan(payload)

    # ==========================================================================
    # MCP configuration
    # ==========================================================================
    async def get_mcp_config(self) -> dict[str, Any]:
        return await self.mcp.get_mcp_config()

    async def save_mcp_config(self, payload: dict[str, Any]) -> None:
        return await self.mcp.save_mcp_config(payload)

    # ==========================================================================
    # Plugins configuration
    # ==========================================================================
    async def get_plugins_config(self) -> dict[str, Any]:
        return await self.plugins.get_plugins_config()

    async def save_plugins_config(self, payload: dict[str, Any]) -> None:
        return await self.plugins.save_plugins_config(payload)

    # ==========================================================================
    # Marketplaces configuration
    # ==========================================================================
    async def get_marketplaces_config(self) -> dict[str, Any]:
        return await self.plugins.get_marketplaces_config()

    async def save_marketplaces_config(self, payload: dict[str, Any]) -> None:
        return await self.plugins.save_marketplaces_config(payload)

    # ==========================================================================
    # Knowledge configuration
    # ==========================================================================
    async def get_knowledge_config(self) -> dict[str, Any]:
        return await self.knowledge.get_knowledge_config()

    async def save_knowledge_config(self, payload: dict[str, Any]) -> None:
        return await self.knowledge.save_knowledge_config(payload)

    async def get_indexer_config(self) -> dict[str, Any]:
        return await self.knowledge.get_indexer_config()

    async def save_indexer_config(self, payload: dict[str, Any]) -> None:
        return await self.knowledge.save_indexer_config(payload)

    async def get_knowledge_index(self) -> dict[str, Any]:
        return await self.knowledge.get_knowledge_index()

    # ==========================================================================
    # User preferences (profile)
    # ==========================================================================
    async def get_preferences_config(self) -> dict[str, Any]:
        return await self.settings.get_preferences_config()

    async def save_preferences_config(self, payload: dict[str, Any]) -> None:
        return await self.settings.save_preferences_config(payload)

    # ==========================================================================
    # Model profiles
    # ==========================================================================
    async def get_model_profiles_config(self) -> dict[str, Any]:
        return await self.settings.get_model_profiles_config()

    async def save_model_profiles_config(self, payload: dict[str, Any]) -> None:
        return await self.settings.save_model_profiles_config(payload)

    # ==========================================================================
    # Kanban board
    # ==========================================================================
    async def get_kanban_structure(self) -> dict[str, Any]:
        return await self.tasks.get_kanban_structure()

    async def save_kanban_structure(self, structure: dict[str, Any]) -> None:
        return await self.tasks.save_kanban_structure(structure)

    async def list_kanban_card_ids(self) -> list[str]:
        return await self.tasks.list_kanban_card_ids()

    async def get_kanban_card(self, card_id: str) -> dict[str, Any] | None:
        return await self.tasks.get_kanban_card(card_id)

    async def save_kanban_card(self, card_id: str, card: dict[str, Any]) -> None:
        return await self.tasks.save_kanban_card(card_id, card)

    async def delete_kanban_card(self, card_id: str) -> None:
        return await self.tasks.delete_kanban_card(card_id)

    async def get_kanban_board(self) -> dict[str, Any]:
        return await self.tasks.get_kanban_board()

    # ==========================================================================
    # Inbox messages
    # ==========================================================================

    async def get_inbox_message(self, message_id: str) -> dict[str, Any] | None:
        return await self.inbox.get_inbox_message(message_id)

    async def save_inbox_message(self, message_id: str, message: dict[str, Any]) -> None:
        return await self.inbox.save_inbox_message(message_id, message)

    async def delete_inbox_message(self, message_id: str) -> None:
        return await self.inbox.delete_inbox_message(message_id)

    async def list_inbox_messages(self) -> list[dict[str, Any]]:
        return await self.inbox.list_inbox_messages()

    # ==========================================================================
    # Session documents
    # ==========================================================================
    async def get_session(self, thread_id: str) -> dict[str, Any] | None:
        return await self.sessions.get_session(thread_id)

    async def list_sessions(self) -> list[dict[str, Any]]:
        return await self.sessions.list_sessions()

    async def merge_session(self, thread_id: str, fields: dict[str, Any]) -> bool:
        return await self.sessions.merge_session(thread_id, fields)

    # ==========================================================================
    # Workflow definitions
    # ==========================================================================
    async def get_workflow_definition(self, workflow_id: str) -> dict[str, Any] | None:
        return await self.workflows.get_workflow_definition(workflow_id)

    async def save_workflow_definition(self, workflow_id: str, payload: dict[str, Any]) -> None:
        return await self.workflows.save_workflow_definition(workflow_id, payload)

    async def delete_workflow_definition(self, workflow_id: str) -> None:
        return await self.workflows.delete_workflow_definition(workflow_id)

    async def list_workflow_definitions(self) -> list[dict[str, Any]]:
        return await self.workflows.list_workflow_definitions()

    # ==========================================================================
    # Document types
    # ==========================================================================
    async def list_document_types(self) -> dict[str, DocumentType]:
        return await self.documents.list_document_types()

    async def _seed_document_types(self) -> None:
        return await self.documents._seed_document_types()

    # ==========================================================================
    # Routine graphs
    # ==========================================================================
    async def get_execution_policy(self, policy_id: str) -> dict[str, Any] | None:
        return await self.settings.get_execution_policy(policy_id)

    async def save_execution_policy(self, policy_id: str, payload: dict[str, Any]) -> None:
        return await self.settings.save_execution_policy(policy_id, payload)

    async def delete_execution_policy(self, policy_id: str) -> None:
        return await self.settings.delete_execution_policy(policy_id)

    async def list_execution_policies(self) -> list[dict[str, Any]]:
        return await self.settings.list_execution_policies()

    async def get_routine_graph(self, agent_id: str) -> dict[str, Any] | None:
        return await self.routines.get_routine_graph(agent_id)

    async def save_routine_graph(self, agent_id: str, payload: dict[str, Any]) -> None:
        return await self.routines.save_routine_graph(agent_id, payload)

    async def delete_routine_graph(self, agent_id: str) -> None:
        return await self.routines.delete_routine_graph(agent_id)

    async def list_routine_graphs(self) -> list[dict[str, Any]]:
        return await self.routines.list_routine_graphs()

    async def list_workflow_runs(self) -> list[dict[str, Any]]:
        return await self.workflows.list_workflow_runs()

    async def request_workflow_run(
        self, *, request_id: str, workflow_id: str, input: dict[str, Any], ancestry: list[str]
    ) -> None:
        return await self.workflows.request_workflow_run(
            request_id=request_id, workflow_id=workflow_id, input=input, ancestry=ancestry
        )

    # ==========================================================================
    # Alerts (one ``alert:<id>`` document each; the backend relays new ones)
    # ==========================================================================
    async def save_alert(self, alert: dict[str, Any]) -> None:
        return await self.alerts.save_alert(alert)

    async def list_alerts(self) -> list[dict[str, Any]]:
        return await self.alerts.list_alerts()

    async def request_workflow_approval(
        self, *, request_id: str, workflow_id: str, run_id: str, approved: bool
    ) -> None:
        return await self.workflows.request_workflow_approval(
            request_id=request_id, workflow_id=workflow_id, run_id=run_id, approved=approved
        )

    # ==========================================================================
    # Content documents (prompts, skills, runtime memories)
    # ==========================================================================
    async def get_content_document(self, kind: str, relative_path: str) -> str | None:
        return await self.documents.get_content_document(kind, relative_path)

    async def save_content_document(self, kind: str, relative_path: str, content: str) -> None:
        return await self.documents.save_content_document(kind, relative_path, content)

    async def delete_content_document(self, kind: str, relative_path: str) -> None:
        return await self.documents.delete_content_document(kind, relative_path)

    async def list_content_documents(self, kind: str) -> list[tuple[str, str]]:
        return await self.documents.list_content_documents(kind)

    async def get_canvas_status(self, name: str) -> dict[str, Any] | None:
        return await self.documents.get_canvas_status(name)

    async def get_knowledge_page(self, base_id: str, path: str) -> dict[str, Any] | None:
        return await self.knowledge.get_knowledge_page(base_id, path)

    async def save_knowledge_page(self, base_id: str, path: str, content: str) -> None:
        return await self.knowledge.save_knowledge_page(base_id, path, content)

    async def list_knowledge_pages(self) -> list[tuple[str, str, dict[str, Any]]]:
        return await self.knowledge.list_knowledge_pages()

    # ==========================================================================
    # MCP tools
    # ==========================================================================
    async def get_mcp_tools(self) -> dict[str, Any]:
        return await self.mcp.get_mcp_tools()

    async def save_mcp_tools(self, payload: dict[str, Any]) -> None:
        return await self.mcp.save_mcp_tools(payload)

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
        return await self.a2a.get_a2a_tasks()

    async def save_a2a_tasks(self, tasks: list[A2ATaskRecord]) -> None:
        return await self.a2a.save_a2a_tasks(tasks)

    async def get_active_a2a_tasks(self) -> list[A2ATaskRecord]:
        return await self.a2a.get_active_a2a_tasks()

    async def get_a2a_task(self, task_id: str) -> A2ATaskRecord | None:
        return await self.a2a.get_a2a_task(task_id)

    async def upsert_a2a_task(self, task: A2ATaskRecord) -> None:
        return await self.a2a.upsert_a2a_task(task)

    async def update_a2a_task(self, task_id: str, updates: dict[str, Any]) -> bool:
        return await self.a2a.update_a2a_task(task_id, updates)

    # ==========================================================================
    # ACP Task Tracking
    # ==========================================================================

    async def get_acp_tasks(self) -> list[ACPTaskRecord]:
        return await self.acp.get_acp_tasks()

    async def get_acp_task(self, task_id: str) -> ACPTaskRecord | None:
        return await self.acp.get_acp_task(task_id)

    async def upsert_acp_task(self, task: ACPTaskRecord) -> None:
        return await self.acp.upsert_acp_task(task)

    async def update_acp_task(self, task_id: str, updates: dict[str, Any]) -> bool:
        return await self.acp.update_acp_task(task_id, updates)

    async def append_acp_task_message(self, task_id: str, message: dict[str, Any]) -> bool:
        return await self.acp.append_acp_task_message(task_id, message)

    async def set_acp_task_answer(self, task_id: str, input_id: str, answer: Any) -> bool:
        return await self.acp.set_acp_task_answer(task_id, input_id, answer)

    # ==========================================================================
    # Async Subagent Task Tracking
    # ==========================================================================

    async def get_async_subagent_tasks(self) -> list[AsyncSubagentTaskRecord]:
        return await self.async_subagents.get_async_subagent_tasks()

    async def save_async_subagent_tasks(self, tasks: list[AsyncSubagentTaskRecord]) -> None:
        return await self.async_subagents.save_async_subagent_tasks(tasks)

    async def get_async_subagent_task(self, task_id: str) -> AsyncSubagentTaskRecord | None:
        return await self.async_subagents.get_async_subagent_task(task_id)

    async def upsert_async_subagent_task(self, task: AsyncSubagentTaskRecord) -> None:
        return await self.async_subagents.upsert_async_subagent_task(task)

    async def update_async_subagent_task(self, task_id: str, updates: dict[str, Any]) -> bool:
        return await self.async_subagents.update_async_subagent_task(task_id, updates)

    # ==========================================================================
    # Background Task Queue
    # ==========================================================================

    async def get_background_tasks(self) -> list[BackgroundTaskEntry]:
        return await self.background.get_background_tasks()

    async def save_background_tasks(self, tasks: list[BackgroundTaskEntry]) -> None:
        return await self.background.save_background_tasks(tasks)

    async def enqueue_background_task(self, task: BackgroundTaskEntry) -> None:
        return await self.background.enqueue_background_task(task)

    async def dequeue_background_task(self) -> BackgroundTaskEntry | None:
        return await self.background.dequeue_background_task()

    # ==========================================================================
    # Schedule Run Tracking
    # ==========================================================================

    async def get_schedule_runs(self) -> list[ScheduleRunRecord]:
        return await self.schedules.get_schedule_runs()

    async def save_schedule_runs(self, runs: list[ScheduleRunRecord]) -> None:
        return await self.schedules.save_schedule_runs(runs)

    async def get_schedule_run(self, run_id: str) -> ScheduleRunRecord | None:
        return await self.schedules.get_schedule_run(run_id)

    async def upsert_schedule_run(self, run: ScheduleRunRecord) -> None:
        return await self.schedules.upsert_schedule_run(run)

    async def update_schedule_run(self, run_id: str, updates: dict[str, Any]) -> bool:
        return await self.schedules.update_schedule_run(run_id, updates)

    # ==========================================================================
    # Messages Management (stored in current_state document)
    # ==========================================================================

    async def commit_state(self) -> None:
        """Commit all cached state to the runtime store."""
        for name, data in self._state_cache.items():
            if name not in self.CORE_FILES:
                continue
            await self._runtime_store.save_document(name, data)
