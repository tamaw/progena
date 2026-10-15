"""Workflow definition storage and backend execution helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from app.backend.document_store import PROGENITOR_STATE_PREFIX, BackendDocumentStore
from app.progenitor.utils import utcnow_isoformat
from app.progenitor.workflows import (
    KNOWLEDGE_INDEX_EVENT_TYPES,
    MAX_WORKFLOW_INVOKE_DEPTH,
    WORKFLOW_ALLOWED_EVENT_TYPES,
    WorkflowDefinition,
    WorkflowListItem,
    WorkflowNodeAgent,
    WorkflowNodeApproval,
    WorkflowNodeCode,
    WorkflowNodeCondition,
    WorkflowNodeEvent,
    WorkflowNodeInbox,
    WorkflowNodePrompt,
    WorkflowNodeUser,
    WorkflowRun,
    WorkflowRunSource,
    WorkflowTrigger,
    build_workflow_list_item,
    coerce_workflow_definition,
    coerce_workflow_run,
    validate_workflow_definition_payload,
    workflow_definition_key,
    workflow_id_from_path,
    workflow_queue_key,
    workflow_run_key,
)

logger = logging.getLogger(__name__)

_ACTIVE_RUN_STATUSES = frozenset({"queued", "running", "paused"})


@dataclass(frozen=True)
class PromptExecutionResult:
    """Minimal result surface the executor needs from a prompt invocation."""

    response: str
    thread_id: str | None = None


class WorkflowPromptRunner(Protocol):
    """Prompt runner adapter for workflow prompt/user/code nodes."""

    async def __call__(
        self,
        *,
        workflow: WorkflowDefinition,
        node_id: str,
        node: (
            WorkflowNodePrompt
            | WorkflowNodeUser
            | WorkflowNodeCode
            | WorkflowNodeCondition
            | WorkflowNodeApproval
            | WorkflowNodeInbox
            | WorkflowNodeAgent
        ),
        input_text: str,
        thread_id: str | None,
    ) -> PromptExecutionResult: ...


def list_workflow_definitions(store: BackendDocumentStore) -> list[WorkflowDefinition]:
    """Return every valid stored workflow definition, newest first."""

    rows = store.list_documents(PROGENITOR_STATE_PREFIX, "workflow:")
    definitions: list[WorkflowDefinition] = []
    for _key, document in rows:
        try:
            definitions.append(coerce_workflow_definition(_document_payload(document, {})))
        except Exception:
            logger.warning("Skipping invalid workflow definition document", exc_info=True)
    definitions.sort(
        key=lambda item: (item.updated_at, item.created_at, item.id),
        reverse=True,
    )
    return definitions


def list_workflow_list_items(store: BackendDocumentStore) -> list[WorkflowListItem]:
    """Return workflow sidebar/list payloads."""

    return [build_workflow_list_item(item) for item in list_workflow_definitions(store)]


def get_workflow_definition(
    store: BackendDocumentStore,
    workflow_id_or_path: str,
) -> WorkflowDefinition | None:
    """Return one stored workflow definition."""

    workflow_id = workflow_id_from_path(workflow_id_or_path)
    document = store.get_document(
        PROGENITOR_STATE_PREFIX, workflow_definition_key(workflow_id), None
    )
    if document is None:
        return None
    return coerce_workflow_definition(_document_payload(document, {}))


def save_workflow_definition(
    store: BackendDocumentStore,
    payload: Mapping[str, Any],
) -> WorkflowDefinition:
    """Validate and persist one workflow definition."""

    normalized = validate_workflow_definition_payload(dict(payload))
    definition = WorkflowDefinition.model_validate(normalized)
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        workflow_definition_key(definition.id),
        definition.model_dump(mode="json", by_alias=True),
    )
    return definition


def delete_workflow_definition(store: BackendDocumentStore, workflow_id_or_path: str) -> None:
    """Delete one workflow definition."""

    workflow_id = workflow_id_from_path(workflow_id_or_path)
    store.delete_document(PROGENITOR_STATE_PREFIX, workflow_definition_key(workflow_id))


def list_workflow_runs(
    store: BackendDocumentStore,
    *,
    workflow_id: str | None = None,
) -> list[WorkflowRun]:
    """Return persisted workflow runs, newest first."""

    rows = store.list_documents(PROGENITOR_STATE_PREFIX, "workflow_run:")
    runs: list[WorkflowRun] = []
    for _key, document in rows:
        try:
            run = coerce_workflow_run(_document_payload(document, {}))
        except Exception:
            logger.warning("Skipping invalid workflow run document", exc_info=True)
            continue
        if workflow_id is not None and run.workflow_id != workflow_id:
            continue
        runs.append(run)
    runs.sort(
        key=lambda item: (item.updated_at, item.queued_at, item.run_id),
        reverse=True,
    )
    return runs


def get_workflow_run(store: BackendDocumentStore, run_id: str) -> WorkflowRun | None:
    """Return one stored workflow run."""

    document = store.get_document(PROGENITOR_STATE_PREFIX, workflow_run_key(run_id), None)
    if document is None:
        return None
    return coerce_workflow_run(_document_payload(document, {}))


def save_workflow_run(store: BackendDocumentStore, run: WorkflowRun) -> WorkflowRun:
    """Persist one workflow run."""

    store.save_document(
        PROGENITOR_STATE_PREFIX,
        workflow_run_key(run.run_id),
        run.model_dump(mode="json"),
    )
    return run


def delete_workflow_run(store: BackendDocumentStore, run_id: str) -> None:
    """Delete one workflow run."""

    store.delete_document(PROGENITOR_STATE_PREFIX, workflow_run_key(run_id))


def get_workflow_queue(store: BackendDocumentStore, workflow_id: str) -> list[dict[str, Any]]:
    """Return queued trigger payloads for one workflow."""

    document = store.get_document(PROGENITOR_STATE_PREFIX, workflow_queue_key(workflow_id), None)
    if document is None:
        return []
    payload = _document_payload(document, {})
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def save_workflow_queue(
    store: BackendDocumentStore,
    workflow_id: str,
    items: list[dict[str, Any]],
) -> None:
    """Persist queued trigger payloads for one workflow."""

    if items:
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            workflow_queue_key(workflow_id),
            {"items": [item for item in items if isinstance(item, dict)]},
        )
        return
    store.delete_document(PROGENITOR_STATE_PREFIX, workflow_queue_key(workflow_id))


def find_active_workflow_run(
    store: BackendDocumentStore,
    workflow_id: str,
) -> WorkflowRun | None:
    """Return the newest active run for one workflow, if any."""

    for run in list_workflow_runs(store, workflow_id=workflow_id):
        if run.status in _ACTIVE_RUN_STATUSES:
            return run
    return None


def build_manual_trigger_event(
    definition: WorkflowDefinition,
    *,
    event_type: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a trigger payload for a manual operator-triggered workflow run."""

    resolved_type = str(event_type or definition.trigger.event_type).strip().lower()
    if resolved_type not in WORKFLOW_ALLOWED_EVENT_TYPES:
        raise ValueError(f"unsupported workflow trigger event type: {resolved_type}")
    timestamp = utcnow_isoformat()
    event_payload = dict(payload or {})
    event_payload.setdefault("event_type", resolved_type)
    event_payload.setdefault("timestamp", timestamp)
    event_payload.setdefault("source", "manual")
    return {
        "event_type": resolved_type,
        "timestamp": timestamp,
        "payload": event_payload,
    }


def compose_workflow_prompt_input(trigger_event: Mapping[str, Any], previous_output: str) -> str:
    """Compose the default prompt input for a workflow prompt node."""

    event_type = str(trigger_event.get("event_type") or "").strip()
    payload = trigger_event.get("payload")
    payload_dict = payload if isinstance(payload, Mapping) else {}
    card = payload_dict.get("card") if isinstance(payload_dict.get("card"), Mapping) else None
    card_title = str(card.get("title") or "").strip() if isinstance(card, Mapping) else ""
    summary_suffix = f" for card '{card_title}'" if card_title else ""
    sections = [
        f"Event summary: {event_type or 'workflow event'}{summary_suffix}",
        "Event payload JSON:",
        json.dumps(payload_dict, indent=2, sort_keys=True, default=str),
    ]
    if previous_output.strip():
        sections.extend(
            [
                "",
                "Previous workflow node output:",
                previous_output,
            ]
        )
    return "\n".join(sections)


def build_user_node_instruction(node: WorkflowNodeUser, input_text: str) -> str:
    """Build the scripted tool-call instruction sent to the agent for a user node.

    Mirrors the old Workspace canvas's ``buildUserMessageInstruction``: the
    agent has no dedicated "send to user" execution path of its own, so we
    ask it to call the matching tool with an exact payload and reply with a
    fixed sentinel, keeping the run's output deterministic.
    """

    message = node.message.strip()
    if input_text.strip():
        message = (
            f"{message}\n\nConnected input:\n{input_text.strip()}"
            if message
            else input_text.strip()
        )

    if node.message_type == "notify_user":
        payload = {
            "title": str(node.notification_title or "").strip(),
            "body": message,
            "level": node.level,
        }
        tool = "notify_user"
    elif node.message_type == "ask_user":
        payload = {"question": message}
        tool = "ask_user"
    else:
        payload = {"message": message}

        tool = "say_to_user"

    return "\n".join(
        [
            f"Use the `{tool}` tool exactly once with this exact payload.",
            "Do not call any other tools.",
            "After the tool call, reply with exactly: sent",
            "```json",
            json.dumps(payload, indent=2),
            "```",
        ]
    )


def build_agent_node_instruction(node: WorkflowNodeAgent, input_text: str) -> str:
    """Ask the runtime's unified call_agent tool to invoke A2A or ACP."""

    request = node.request.strip()
    if input_text.strip():
        request = f"{request}\n\nConnected workflow input:\n{input_text.strip()}"
    return "\n".join(
        [
            "Use the `call_agent` tool exactly once.",
            f"Select the registered {node.transport.upper()} agent named `{node.agent_name}`.",
            f"Use this request:\n{request}",
            f"Wait for completion with timeout_seconds={node.timeout_seconds}.",
            "After the tool call, reply with only the returned agent result.",
        ]
    )


def render_workflow_event_output(trigger_event: Mapping[str, Any]) -> str:
    """Render the event node's output text for downstream prompt nodes."""

    payload = trigger_event.get("payload")
    payload_dict = payload if isinstance(payload, Mapping) else {}
    event_type = str(
        trigger_event.get("event_type") or payload_dict.get("event_type") or ""
    ).strip()
    return json.dumps(
        {
            "event_type": event_type,
            "payload": payload_dict,
        },
        indent=2,
        sort_keys=True,
        default=str,
    )


def workflow_outgoing_edges(
    definition: WorkflowDefinition,
    node_id: str,
) -> list[tuple[str, str]]:
    """Return outgoing workflow edges as (port, target) pairs."""

    return [
        (edge.from_.port, edge.to.node)
        for edge in definition.graph.edges
        if edge.from_.node == node_id
    ]


def evaluate_workflow_condition(
    expression: str,
    trigger_event: Mapping[str, Any],
    previous_output: str,
) -> bool:
    """Evaluate the deliberately small, non-code condition language."""

    raw = expression.strip()
    context: dict[str, Any] = {
        "payload": trigger_event.get("payload")
        if isinstance(trigger_event.get("payload"), Mapping)
        else {},
        "event_type": str(trigger_event.get("event_type") or ""),
        "previous_output": previous_output,
    }
    if raw in context:
        return bool(context[raw])
    for operator in ("==", "!="):
        if operator not in raw:
            continue
        left, right = (part.strip() for part in raw.split(operator, 1))
        value: Any = context.get(left)
        if left.startswith("payload."):
            value = context["payload"]
            for part in left.removeprefix("payload.").split("."):
                value = value.get(part) if isinstance(value, Mapping) else None
        try:
            expected = json.loads(right)
        except json.JSONDecodeError:
            expected = right.strip("'\"")
        return bool(value == expected) if operator == "==" else bool(value != expected)
    raise ValueError("condition must be a value path or a == / != comparison")


def save_workflow_inbox_message(
    store: BackendDocumentStore,
    *,
    title: str,
    body: str,
    level: str,
) -> str:
    """Persist one workflow-generated inbox message."""

    message_id = f"workflow_{uuid.uuid4().hex[:12]}"
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        f"inbox_message:{message_id}",
        {
            "id": message_id,
            "title": title,
            "body": body,
            "level": level,
            "read": False,
            "created_at": utcnow_isoformat(),
            "source": "workflow",
        },
    )
    return message_id


def _trigger_column_filter_matches(trigger: WorkflowTrigger, event: Mapping[str, Any]) -> bool:
    """Check a trigger's optional from_column/to_column filter against a kanban.card.moved payload.

    Other event types carry no column transition, so a trigger with a column
    filter set simply never matches them.
    """

    if not trigger.from_column and not trigger.to_column:
        return True
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return False
    from_column = (
        (payload.get("from") or {}).get("column_id")
        if isinstance(payload.get("from"), dict)
        else None
    )
    to_column = (
        (payload.get("to") or {}).get("column_id") if isinstance(payload.get("to"), dict) else None
    )
    return (not trigger.from_column or trigger.from_column == from_column) and (
        not trigger.to_column or trigger.to_column == to_column
    )


def _document_trigger_filter_matches(trigger: WorkflowTrigger, event: Mapping[str, Any]) -> bool:
    """Check a trigger's optional key_prefix/key/action filter against a document.changed payload.

    Each field is a wildcard when unset on the trigger, so a trigger with no
    filters set matches every document.changed event.
    """

    if not trigger.key_prefix and not trigger.key and not trigger.action:
        return True
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return False
    return (
        (not trigger.key_prefix or trigger.key_prefix == payload.get("key_prefix"))
        and (not trigger.key or trigger.key == payload.get("key"))
        and (not trigger.action or trigger.action == payload.get("action"))
    )


def _knowledge_index_trigger_filter_matches(
    trigger: WorkflowTrigger, event: Mapping[str, Any]
) -> bool:
    """Check a trigger's optional key filter against a knowledge.index.* payload."""

    if not trigger.key:
        return True
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return False
    return trigger.key == payload.get("key")


def _trigger_matches(event_type: str, trigger: WorkflowTrigger, event: Mapping[str, Any]) -> bool:
    """Pick the right filter matcher for the trigger's event type."""

    if event_type == "document.changed":
        return _document_trigger_filter_matches(trigger, event)
    if event_type in KNOWLEDGE_INDEX_EVENT_TYPES:
        return _knowledge_index_trigger_filter_matches(trigger, event)
    return _trigger_column_filter_matches(trigger, event)


def _build_queued_run(
    workflow_id: str,
    trigger: Mapping[str, Any],
    *,
    now: str,
) -> WorkflowRun:
    """Construct a queued run, preserving invocation metadata."""

    invocation = trigger.get("_invocation")
    invocation = invocation if isinstance(invocation, Mapping) else {}
    raw_source = invocation.get("source")
    source: WorkflowRunSource = (
        raw_source if raw_source in ("kanban", "manual", "progenitor") else "manual"
    )
    ancestry_raw = invocation.get("ancestry")
    ancestry = [str(item) for item in ancestry_raw] if isinstance(ancestry_raw, list) else []
    return WorkflowRun(
        run_id=f"workflow_run_{uuid.uuid4().hex[:12]}",
        workflow_id=workflow_id,
        status="queued",
        trigger_event=dict(trigger),
        source=source,
        ancestry=ancestry,
        queued_at=now,
        updated_at=now,
    )


class WorkflowExecutor:
    """Backend-resident workflow executor for v1 event -> prompt chains."""

    def __init__(
        self,
        *,
        store_getter: Callable[[], BackendDocumentStore],
        prompt_runner: WorkflowPromptRunner,
        emit_event: Callable[[str, Mapping[str, Any]], Any],
    ) -> None:
        self._store_getter = store_getter
        self._prompt_runner = prompt_runner
        self._emit_event = emit_event
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def dispatch_triggers(self, triggers: list[dict[str, Any]]) -> list[WorkflowRun]:
        """Dispatch a batch of trigger payloads to matching workflows."""

        started: list[WorkflowRun] = []
        for trigger in triggers:
            event_type = str(trigger.get("event_type") or "").strip().lower()
            if not event_type:
                continue
            started.extend(await self.dispatch_event(event_type, trigger))
        return started

    async def dispatch_event(self, event_type: str, trigger: dict[str, Any]) -> list[WorkflowRun]:
        """Dispatch one event payload to all matching workflows."""

        store = self._store_getter()
        definitions = [
            definition
            for definition in list_workflow_definitions(store)
            if definition.trigger.event_type == event_type
            and _trigger_matches(event_type, definition.trigger, trigger)
        ]
        started: list[WorkflowRun] = []
        for definition in definitions:
            run = await self.trigger_workflow(definition.id, trigger, source="kanban")
            if run is not None:
                started.append(run)
        return started

    async def trigger_workflow(
        self,
        workflow_id: str,
        trigger: Mapping[str, Any],
        *,
        source: WorkflowRunSource = "manual",
        ancestry: list[str] | None = None,
    ) -> WorkflowRun | None:
        """Start or queue a run with bounded, cycle-safe ancestry."""

        resolved_ancestry = [str(item) for item in (ancestry or [])]
        if workflow_id in resolved_ancestry:
            logger.warning(
                "Refusing workflow %r: already present in ancestry %r",
                workflow_id,
                resolved_ancestry,
            )
            return None
        if len(resolved_ancestry) >= MAX_WORKFLOW_INVOKE_DEPTH:
            logger.warning(
                "Refusing workflow %r: invocation depth %d reached limit %d",
                workflow_id,
                len(resolved_ancestry),
                MAX_WORKFLOW_INVOKE_DEPTH,
            )
            return None
        trigger_with_meta = {
            **dict(trigger),
            "_invocation": {"source": source, "ancestry": resolved_ancestry},
        }

        async with self._lock_for(workflow_id):
            store = self._store_getter()
            definition = get_workflow_definition(store, workflow_id)
            if definition is None or not definition.enabled or definition.paused:
                return None

            active_run = find_active_workflow_run(store, workflow_id)
            if active_run is not None:
                queue = get_workflow_queue(store, workflow_id)
                queue.append(trigger_with_meta)
                save_workflow_queue(store, workflow_id, queue)
                return active_run

            now = utcnow_isoformat()
            run = _build_queued_run(workflow_id, trigger_with_meta, now=now)
            save_workflow_run(store, run)
            self._start_task(workflow_id, run.run_id)
            return run

    async def pause_workflow(self, workflow_id: str) -> WorkflowRun | None:
        """Pause the active run for one workflow, if present."""

        async with self._lock_for(workflow_id):
            store = self._store_getter()
            active_run = find_active_workflow_run(store, workflow_id)
            if active_run is None:
                return None
            if active_run.status != "paused":
                active_run = active_run.model_copy(
                    update={
                        "status": "paused",
                        "updated_at": utcnow_isoformat(),
                    }
                )
                save_workflow_run(store, active_run)
                self._emit_event("workflow.run.updated", active_run.model_dump(mode="json"))
            return active_run

    async def resume_workflow(self, workflow_id: str) -> WorkflowRun | None:
        """Resume a paused run or start the next queued trigger."""

        candidate: WorkflowRun | None = None
        async with self._lock_for(workflow_id):
            store = self._store_getter()
            active_run = find_active_workflow_run(store, workflow_id)
            if active_run is not None and active_run.status == "paused":
                candidate = active_run.model_copy(
                    update={
                        "status": "queued",
                        "updated_at": utcnow_isoformat(),
                    }
                )
                save_workflow_run(store, candidate)
            elif active_run is None:
                queue = get_workflow_queue(store, workflow_id)
                if queue:
                    now = utcnow_isoformat()
                    candidate = _build_queued_run(workflow_id, queue.pop(0), now=now)
                    save_workflow_run(store, candidate)
                    save_workflow_queue(store, workflow_id, queue)
        if candidate is not None:
            self._start_task(workflow_id, candidate.run_id)
        return candidate

    async def resolve_approval(
        self,
        workflow_id: str,
        run_id: str,
        *,
        approved: bool,
    ) -> WorkflowRun | None:
        """Resolve a paused approval node and continue its run."""

        async with self._lock_for(workflow_id):
            store = self._store_getter()
            run = get_workflow_run(store, run_id)
            if (
                run is None
                or run.workflow_id != workflow_id
                or run.status != "paused"
                or run.waiting_node_id is None
            ):
                return None
            updated = run.model_copy(
                update={
                    "status": "queued",
                    "pending_approval": {
                        **(run.pending_approval or {}),
                        "approved": approved,
                        "resolved_at": utcnow_isoformat(),
                    },
                    "last_output": "approved" if approved else "rejected",
                    "updated_at": utcnow_isoformat(),
                }
            )
            save_workflow_run(store, updated)
        self._start_task(workflow_id, run_id)
        return updated

    def _lock_for(self, workflow_id: str) -> asyncio.Lock:
        lock = self._locks.get(workflow_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[workflow_id] = lock
        return lock

    def _start_task(self, workflow_id: str, run_id: str) -> None:
        existing = self._tasks.get(workflow_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self._execute_run(workflow_id, run_id))
        self._tasks[workflow_id] = task

        def _cleanup(_task: asyncio.Task[None]) -> None:
            current = self._tasks.get(workflow_id)
            if current is _task:
                self._tasks.pop(workflow_id, None)

        task.add_done_callback(_cleanup)

    async def _execute_run(self, workflow_id: str, run_id: str) -> None:
        try:
            await self._execute_run_locked(workflow_id, run_id)
        except Exception:
            logger.exception("Workflow execution failed for %s", workflow_id)

    async def _execute_run_locked(self, workflow_id: str, run_id: str) -> None:
        store = self._store_getter()
        run = get_workflow_run(store, run_id)
        if run is None:
            return
        definition = get_workflow_definition(store, workflow_id)
        if definition is None:
            self._mark_failed(
                store,
                run,
                error="Workflow definition no longer exists",
            )
            await self._maybe_start_next_run_locked(store, workflow_id)
            return
        if not definition.enabled:
            self._mark_failed(store, run, error="Workflow is disabled")
            await self._maybe_start_next_run_locked(store, workflow_id)
            return
        if definition.paused or run.status == "paused":
            paused_run = run.model_copy(
                update={"status": "paused", "updated_at": utcnow_isoformat()}
            )
            save_workflow_run(store, paused_run)
            self._emit_event("workflow.run.updated", paused_run.model_dump(mode="json"))
            return

        if run.status == "queued":
            run = run.model_copy(
                update={
                    "status": "running",
                    "started_at": run.started_at or utcnow_isoformat(),
                    "updated_at": utcnow_isoformat(),
                }
            )
            save_workflow_run(store, run)
            self._emit_event("workflow.run.started", run.model_dump(mode="json"))

        completed = list(run.completed_node_ids)
        last_output = str(run.last_output or "")
        thread_id = run.thread_id
        if completed:
            previous_node_id = completed[-1]
            previous_node = definition.graph.nodes.get(previous_node_id)
            previous_port = (
                last_output
                if isinstance(previous_node, WorkflowNodeCondition | WorkflowNodeApproval)
                else "default"
            )
            outgoing = workflow_outgoing_edges(definition, previous_node_id)
            node_id = next(
                (target for port, target in outgoing if port == previous_port),
                None,
            )
        else:
            node_id = next(
                node_id
                for node_id, node in definition.graph.nodes.items()
                if isinstance(node, WorkflowNodeEvent)
            )

        while node_id is not None:
            definition = get_workflow_definition(store, workflow_id) or definition
            latest_run = get_workflow_run(store, run_id) or run
            if not definition.enabled:
                self._mark_failed(store, latest_run, error="Workflow was disabled during execution")
                await self._maybe_start_next_run_locked(store, workflow_id)
                return
            if definition.paused or latest_run.status == "paused":
                paused_run = latest_run.model_copy(
                    update={"status": "paused", "updated_at": utcnow_isoformat()}
                )
                save_workflow_run(store, paused_run)
                self._emit_event("workflow.run.updated", paused_run.model_dump(mode="json"))
                return

            node = definition.graph.nodes[node_id]
            try:
                input_text = compose_workflow_prompt_input(latest_run.trigger_event, last_output)
                if isinstance(node, WorkflowNodeEvent):
                    output_text = render_workflow_event_output(latest_run.trigger_event)
                elif isinstance(node, WorkflowNodeCondition):
                    output_text = (
                        "true"
                        if evaluate_workflow_condition(
                            node.expression, latest_run.trigger_event, last_output
                        )
                        else "false"
                    )
                elif isinstance(node, WorkflowNodeApproval):
                    pending = {
                        "node_id": node_id,
                        "title": node.title,
                        "message": node.message,
                        "requested_at": utcnow_isoformat(),
                    }
                    paused_run = latest_run.model_copy(
                        update={
                            "status": "paused",
                            "current_node_id": node_id,
                            "completed_node_ids": [
                                *latest_run.completed_node_ids,
                                *([] if node_id in latest_run.completed_node_ids else [node_id]),
                            ],
                            "waiting_node_id": node_id,
                            "pending_approval": pending,
                            "updated_at": utcnow_isoformat(),
                        }
                    )
                    save_workflow_run(store, paused_run)
                    self._emit_event("workflow.run.updated", paused_run.model_dump(mode="json"))
                    return
                elif isinstance(node, WorkflowNodeInbox):
                    message_id = save_workflow_inbox_message(
                        store,
                        title=node.message_title,
                        body=f"{node.message}\n\n{input_text}",
                        level=node.level,
                    )
                    output_text = f"inbox message sent: {message_id}"
                else:
                    prompt_result = await self._prompt_runner(
                        workflow=definition,
                        node_id=node_id,
                        node=node,
                        input_text=input_text,
                        thread_id=thread_id,
                    )
                    output_text = prompt_result.response
                    thread_id = prompt_result.thread_id or thread_id
            except Exception as exc:
                self._mark_failed(store, latest_run, error=str(exc))
                await self._maybe_start_next_run_locked(store, workflow_id)
                return

            definition_after = get_workflow_definition(store, workflow_id) or definition
            latest_run_after = get_workflow_run(store, run_id) or latest_run
            completed = list(latest_run_after.completed_node_ids)
            if node_id not in completed:
                completed.append(node_id)
            paused_after_node = definition_after.paused or latest_run_after.status == "paused"
            run = latest_run_after.model_copy(
                update={
                    "status": "paused" if paused_after_node else "running",
                    "current_node_id": node_id,
                    "completed_node_ids": completed,
                    "waiting_node_id": None,
                    "pending_approval": None,
                    "thread_id": thread_id,
                    "last_output": output_text,
                    "updated_at": utcnow_isoformat(),
                }
            )
            last_output = output_text
            save_workflow_run(store, run)
            self._emit_event("workflow.run.updated", run.model_dump(mode="json"))
            if paused_after_node:
                return
            if not definition_after.enabled:
                self._mark_failed(store, run, error="Workflow was disabled during execution")
                await self._maybe_start_next_run_locked(store, workflow_id)
                return

            selected_port = (
                output_text
                if isinstance(node, WorkflowNodeCondition | WorkflowNodeApproval)
                else "default"
            )
            outgoing = workflow_outgoing_edges(definition_after, node_id)
            node_id = next(
                (target for port, target in outgoing if port == selected_port),
                None,
            )

        run = run.model_copy(
            update={
                "status": "completed",
                "updated_at": utcnow_isoformat(),
                "completed_at": utcnow_isoformat(),
            }
        )
        save_workflow_run(store, run)
        self._emit_event("workflow.run.completed", run.model_dump(mode="json"))
        await self._maybe_start_next_run_locked(store, workflow_id)

    async def _maybe_start_next_run_locked(
        self,
        store: BackendDocumentStore,
        workflow_id: str,
    ) -> None:
        async with self._lock_for(workflow_id):
            definition = get_workflow_definition(store, workflow_id)
            if definition is None or not definition.enabled or definition.paused:
                return
            queue = get_workflow_queue(store, workflow_id)
            if not queue:
                save_workflow_queue(store, workflow_id, [])
                return
            now = utcnow_isoformat()
            next_run = _build_queued_run(workflow_id, queue.pop(0), now=now)
            save_workflow_run(store, next_run)
            save_workflow_queue(store, workflow_id, queue)
            # Hand the slot over to the queued run now; otherwise `_start_task()`
            # can see the finishing task still registered and refuse to launch the
            # successor even though this current run is done.
            self._tasks.pop(workflow_id, None)
            asyncio.get_running_loop().call_soon(self._start_task, workflow_id, next_run.run_id)

    def _mark_failed(
        self,
        store: BackendDocumentStore,
        run: WorkflowRun,
        *,
        error: str,
    ) -> WorkflowRun:
        failed = run.model_copy(
            update={
                "status": "failed",
                "last_error": error,
                "updated_at": utcnow_isoformat(),
                "completed_at": utcnow_isoformat(),
            }
        )
        save_workflow_run(store, failed)
        self._emit_event("workflow.run.failed", failed.model_dump(mode="json"))
        return failed


def _document_payload(document: Any, default: Any) -> Any:
    if document is None:
        return default
    payload = getattr(document, "payload", document)
    return payload if payload is not None else default
