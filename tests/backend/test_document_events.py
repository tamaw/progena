import asyncio
from datetime import UTC, datetime

import pytest

from app.backend import ui_routes
from app.backend.document_events import (
    DocumentEventDispatcher,
    TypedDocumentEvent,
    seed_document_types,
)
from app.backend.document_store import (
    EVENT_LOG_START,
    PROGENITOR_STATE_PREFIX,
    DocumentEvent,
    InMemoryDocumentStore,
    document_write_origin,
    set_default_document_store,
)
from app.backend.workflow_service import (
    _execute_document_create_node,
    _execute_document_delete_node,
    _execute_document_node,
    build_manual_trigger_event,
    list_workflow_runs,
    save_workflow_definition,
)
from app.progenitor.document_kinds import KNOWLEDGE_PAGE_KEY_PREFIX, SESSION_KEY_PREFIX
from app.progenitor.document_types import BUILTIN_DOCUMENT_TYPES, resolve_document_type
from app.progenitor.workflows import (
    WorkflowNodeDocumentCreate,
    WorkflowNodeDocumentDelete,
    WorkflowNodeDocumentQuery,
    WorkflowNodeDocumentWrite,
    WorkflowNodeEvent,
    default_workflow_definition,
    derive_workflow_port_schemas,
    validate_workflow_definition_payload,
)

BUILTIN_TYPES = {item.id: item for item in BUILTIN_DOCUMENT_TYPES}


@pytest.fixture
def store():
    store = InMemoryDocumentStore()
    seed_document_types(store)
    set_default_document_store(store)
    ui_routes._workflow_executor = None
    ui_routes._document_event_dispatcher = None
    yield store
    ui_routes._workflow_executor = None
    ui_routes._document_event_dispatcher = None
    set_default_document_store(InMemoryDocumentStore())


async def _wait_for(predicate, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if value := predicate():
            return value
        await asyncio.sleep(0.01)
    raise AssertionError("Timed out waiting for workflow execution")


def _card(card_id, column_id, **fields):
    return {
        "id": card_id,
        "title": fields.pop("title", "Card"),
        "summary": "",
        "column_id": column_id,
        "status": "idle",
        **fields,
    }


def _workflow(event, nodes, edges):
    workflow = default_workflow_definition()
    workflow["graph"] = {
        "nodes": {"event": {"kind": "event", "title": "Event", **event}, **nodes},
        "edges": [
            {"from": {"node": src, "port": src_port}, "to": {"node": dst, "port": dst_port}}
            for src, src_port, dst, dst_port in edges
        ],
    }
    workflow["ui"] = {}
    return workflow


def _completed_runs(store, workflow_id):
    return [
        run
        for run in list_workflow_runs(store, workflow_id=workflow_id)
        if run.status == "completed"
    ]


def test_store_event_log_records_each_operation_and_skips_identical_rewrites():
    store = InMemoryDocumentStore()

    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:a", {"title": "One"})
    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:a", {"title": "One"})
    with document_write_origin("workflow_run_x"):
        store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:a", {"title": "Two"})
    store.delete_document(PROGENITOR_STATE_PREFIX, "kanban_card:a")
    store.save_document("progenitor.logs.tool_calls", "entry", {"ignored": True})

    events = store.read_document_events(EVENT_LOG_START, 10)
    assert [(event.op, event.before, event.after, event.origin) for event in events] == [
        ("created", None, {"title": "One"}, None),
        ("updated", {"title": "One"}, {"title": "Two"}, "workflow_run_x"),
        ("deleted", {"title": "Two"}, None, None),
    ]


@pytest.mark.asyncio
async def test_dispatcher_starts_at_head_and_catches_up_on_events_written_while_stopped():
    store = InMemoryDocumentStore()
    seed_document_types(store)
    seen: list[tuple[str, str]] = []

    async def record(typed: TypedDocumentEvent) -> None:
        if typed.document_type is not None and typed.document_type.id == "kanban_card":
            seen.append((typed.event.op, typed.event.key))

    def build():
        dispatcher = DocumentEventDispatcher(store_getter=lambda: store)
        dispatcher.add_handler(record)
        return dispatcher

    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:history", _card("history", "plan"))
    first = build()
    await first.start()
    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:live", _card("live", "plan"))
    await first.drain()
    await first.stop()

    store.delete_document(PROGENITOR_STATE_PREFIX, "kanban_card:live")
    second = build()
    await second.start()
    await second.drain()
    await second.stop()

    assert seen == [("created", "kanban_card:live"), ("deleted", "kanban_card:live")]


@pytest.mark.asyncio
async def test_new_alert_document_is_relayed_to_the_user(monkeypatch):
    relayed = []

    async def relay(alert):
        relayed.append(alert)

    monkeypatch.setattr(ui_routes, "broadcast_alerts_state", lambda: None)
    monkeypatch.setattr(ui_routes, "broadcast_alert", lambda _alert: None)
    ui_routes.set_alert_relay(relay)
    try:
        await ui_routes._publish_alert_document(
            TypedDocumentEvent(
                event=DocumentEvent(
                    seq=1,
                    txid=1,
                    prefix=PROGENITOR_STATE_PREFIX,
                    key="alert:agent-alert",
                    op="created",
                    before=None,
                    after={
                        "id": "agent-alert",
                        "type": "info",
                        "title": "Build complete",
                        "message": "Tests passed",
                        "timestamp": 1.0,
                        "urgent": False,
                        "requires_response": False,
                        "viewed": False,
                    },
                    origin=None,
                    created_at=datetime.now(UTC),
                ),
                document_type=None,
                output=None,
            )
        )
        await _wait_for(lambda: relayed)
    finally:
        ui_routes.set_alert_relay(None)

    assert relayed[0].id == "agent-alert"


@pytest.mark.asyncio
async def test_handler_failure_stops_dispatch_without_acknowledging_the_event():
    store = InMemoryDocumentStore()
    seed_document_types(store)
    seen: list[str] = []
    failing = {"key": "kanban_card:bad"}

    async def record(typed: TypedDocumentEvent) -> None:
        if typed.event.key == failing["key"]:
            raise RuntimeError("store unavailable")
        seen.append(typed.event.key)

    dispatcher = DocumentEventDispatcher(store_getter=lambda: store)
    dispatcher.add_handler(record)
    await dispatcher.start()
    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:bad", _card("bad", "plan"))
    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:after", _card("after", "plan"))
    await _wait_for(lambda: dispatcher.stopped)
    await dispatcher.stop()

    # Nothing past the failing event ran, and the failing event is still pending.
    assert seen == []
    [pending, *_] = store.read_document_events(
        store.get_document_event_cursor(dispatcher._consumer), 10
    )
    assert pending.key == "kanban_card:bad"

    failing["key"] = None
    await dispatcher.start()
    await dispatcher.drain()
    await dispatcher.stop()
    assert seen == ["kanban_card:bad", "kanban_card:after"]


@pytest.mark.asyncio
async def test_dispatcher_rechecks_while_events_are_deferred(monkeypatch):
    """A committed event hidden behind an open transaction is dispatched once
    that transaction ends, even though nothing wakes the dispatcher."""
    from app.backend import document_events

    monkeypatch.setattr(document_events, "_DEFERRED_RETRY_SECONDS", 0.05)

    class DeferringStore(InMemoryDocumentStore):
        open_transaction = True

        def read_document_events(self, after, limit):
            return [] if self.open_transaction else super().read_document_events(after, limit)

        def has_deferred_document_events(self, after):
            return self.open_transaction and bool(super().read_document_events(after, 1))

    store = DeferringStore()
    seed_document_types(store)
    seen: list[str] = []

    async def record(typed: TypedDocumentEvent) -> None:
        seen.append(typed.event.key)

    dispatcher = DocumentEventDispatcher(store_getter=lambda: store)
    dispatcher.add_handler(record)
    await dispatcher.start()
    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:held", _card("held", "plan"))
    await asyncio.sleep(0.1)
    assert seen == []

    store.open_transaction = False  # ends without a new event or wake
    await _wait_for(lambda: seen == ["kanban_card:held"])
    await dispatcher.stop()


def test_in_memory_event_seq_is_not_reused_after_pruning_everything():
    from datetime import timedelta

    store = InMemoryDocumentStore()
    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:a", {"title": "One"})
    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:b", {"title": "Two"})
    cursor = store.latest_document_event_position()
    assert store.prune_document_events(through=cursor, older_than=timedelta(0)) == 2

    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:c", {"title": "Three"})

    assert [event.key for event in store.read_document_events(cursor, 10)] == ["kanban_card:c"]


@pytest.mark.asyncio
async def test_card_move_to_done_starts_filtered_workflow_once_with_document_ports(store):
    workflow = _workflow(
        {
            "document_type": "kanban_card",
            "operations": ["updated"],
            "changed_fields": ["column_id"],
            "match": {"after.column_id": "done"},
        },
        {"body": {"kind": "document_read", "title": "Task body", "document_type": "kanban_task"}},
        [("event", "id", "body", "id")],
    )
    definition = save_workflow_definition(store, workflow)
    assert definition.graph.nodes["event"].output_schema["properties"]["after"]["title"] == (
        "Kanban Card"
    )
    dispatcher = ui_routes.get_document_event_dispatcher()
    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_task_content:c1", {"content": "# Brief"})
    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:c1", _card("c1", "build"))
    store.save_document(
        PROGENITOR_STATE_PREFIX, "kanban_card:c1", _card("c1", "build", title="Renamed")
    )
    await dispatcher.drain()
    assert list_workflow_runs(store, workflow_id=definition.id) == []

    store.save_document(
        PROGENITOR_STATE_PREFIX, "kanban_card:c1", _card("c1", "done", title="Renamed")
    )
    await dispatcher.drain()
    [run] = await _wait_for(lambda: _completed_runs(store, definition.id))
    await dispatcher.drain()

    assert len(list_workflow_runs(store, workflow_id=definition.id)) == 1
    assert run.trigger_event["before"]["column_id"] == "build"
    assert run.trigger_event["changed_fields"] == ["column_id"]
    assert run.node_outputs["body"]["found"] is True
    assert run.node_outputs["body"]["document"]["content"] == "# Brief"


@pytest.mark.asyncio
async def test_knowledge_page_and_index_events_start_typed_workflows(store):
    page_workflow = save_workflow_definition(
        store,
        _workflow(
            {"document_type": "knowledge_page", "operations": ["created"]},
            {},
            [],
        ),
    )
    index_workflow = save_workflow_definition(
        store,
        _workflow(
            {"document_type": "knowledge_index", "operations": ["updated"]},
            {},
            [],
        ),
    )
    knowledge_workflow = save_workflow_definition(
        store,
        _workflow(
            {"document_type": "knowledge", "operations": ["updated"]},
            {},
            [],
        ),
    )
    dispatcher = ui_routes.get_document_event_dispatcher()
    store.save_document(PROGENITOR_STATE_PREFIX, "knowledge_index", {"bases": {}})
    store.save_document(PROGENITOR_STATE_PREFIX, "knowledge", {"bases": []})
    await dispatcher.drain()

    store.save_document(
        PROGENITOR_STATE_PREFIX,
        f"{KNOWLEDGE_PAGE_KEY_PREFIX}engineering/overview.md",
        {"content": "---\ntype: Concept\ntitle: Overview\n---\n"},
    )
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "knowledge_index",
        {"bases": {"engineering": {"status": "indexed", "pages": 1}}},
    )
    store.save_document(PROGENITOR_STATE_PREFIX, "knowledge", {"bases": [{"id": "engineering"}]})
    await dispatcher.drain()

    page_run = await _wait_for(lambda: _completed_runs(store, page_workflow.id))
    index_run = await _wait_for(lambda: _completed_runs(store, index_workflow.id))
    knowledge_run = await _wait_for(lambda: _completed_runs(store, knowledge_workflow.id))
    assert page_run[0].trigger_event["document_type"] == "knowledge_page"
    assert index_run[0].trigger_event["document_type"] == "knowledge_index"
    assert knowledge_run[0].trigger_event["document_type"] == "knowledge"
    assert resolve_document_type("knowledge", BUILTIN_TYPES).id == "knowledge"


@pytest.mark.asyncio
async def test_workflow_is_not_retriggered_by_its_own_run_documents(store):
    workflow = _workflow(
        {"document_type": "workflow_run"},
        {"read": {"kind": "document_read", "title": "Read", "document_type": "kanban_task"}},
        [("event", "id", "read", "id")],
    )
    definition = save_workflow_definition(store, workflow)
    dispatcher = ui_routes.get_document_event_dispatcher()
    await dispatcher.drain()

    await ui_routes.get_workflow_executor().trigger_workflow(
        definition.id, build_manual_trigger_event(definition)
    )
    await _wait_for(lambda: _completed_runs(store, definition.id))
    await dispatcher.drain()
    await asyncio.sleep(0.05)

    assert len(list_workflow_runs(store, workflow_id=definition.id)) == 1


@pytest.mark.asyncio
async def test_query_node_returns_collection_documents_matching_unpacked_field(store):
    workflow = _workflow(
        {"document_type": "kanban_card", "operations": ["created"]},
        {
            "card": {"kind": "unpack", "title": "Card"},
            "peers": {
                "kind": "document_query",
                "title": "Same column",
                "document_type": "kanban_card",
                "filters": [{"field": "column_id", "op": "eq"}],
            },
        },
        [("event", "after", "card", "value"), ("card", "column_id", "peers", "column_id")],
    )
    definition = save_workflow_definition(store, workflow)
    dispatcher = ui_routes.get_document_event_dispatcher()
    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:a", _card("a", "plan"))
    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:b", _card("b", "build"))
    await dispatcher.drain()
    await _wait_for(lambda: len(_completed_runs(store, definition.id)) == 2)
    await dispatcher.drain()
    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:c", _card("c", "plan"))
    await dispatcher.drain()

    runs = await _wait_for(
        lambda: [
            run
            for run in _completed_runs(store, definition.id)
            if run.trigger_event["key"] == "kanban_card:c"
        ]
    )
    assert runs[0].node_outputs["card"]["column_id"] == "plan"
    assert runs[0].node_outputs["peers"]["keys"] == ["kanban_card:a", "kanban_card:c"]
    assert runs[0].node_outputs["peers"]["count"] == 2


def test_document_node_types_are_validated_against_the_registry():
    unknown = _workflow({"document_type": "nope"}, {}, [])
    with pytest.raises(ValueError, match="unknown document type 'nope'"):
        derive_workflow_port_schemas(unknown, BUILTIN_TYPES)

    single = _workflow(
        {"document_type": "kanban_card"},
        {"q": {"kind": "document_query", "title": "Q", "document_type": "preferences"}},
        [],
    )
    with pytest.raises(ValueError, match="needs a collection document type"):
        derive_workflow_port_schemas(single, BUILTIN_TYPES)


def _nested_data_workflow(inner_port):
    """Data(value: {card: {meta: {owner}}}) -> Unpack -> Unpack(card) -> Agent(meta)."""

    nested = {
        "type": "object",
        "properties": {
            "card": {
                "type": "object",
                "required": ["meta"],
                "properties": {
                    "meta": {
                        "type": "object",
                        "properties": {"owner": {"type": "string"}},
                    }
                },
            }
        },
    }
    return _workflow(
        {"document_type": "kanban_card"},
        {
            "data": {"kind": "data", "title": "Data", "value": {}, "output_schema": nested},
            "outer": {"kind": "unpack", "title": "Outer"},
            "inner": {"kind": "unpack", "title": "Inner"},
            "agent": {
                "kind": "agent",
                "title": "Agent",
                "template": "Go {meta}",
                "input_schema": {
                    "type": "object",
                    "properties": {"meta": {"type": "object"}},
                },
                "output_schema": {"type": "object", "properties": {}},
            },
        },
        # Listed inner-first so derivation cannot rely on edge order.
        [
            ("inner", inner_port, "agent", "meta"),
            ("outer", "card", "inner", "value"),
            ("data", "value", "outer", "value"),
        ],
    )


def test_unpack_chains_derive_ports_from_the_upstream_object():
    derived = derive_workflow_port_schemas(_nested_data_workflow("meta"), BUILTIN_TYPES)
    nodes = derived["graph"]["nodes"]
    assert set(nodes["outer"]["output_schema"]["properties"]) == {"card"}
    assert set(nodes["inner"]["output_schema"]["properties"]) == {"meta"}
    assert nodes["inner"]["output_schema"]["required"] == ["meta"]
    validate_workflow_definition_payload(derived)

    stale = derive_workflow_port_schemas(_nested_data_workflow("owner"), BUILTIN_TYPES)
    with pytest.raises(ValueError, match="has no output port 'owner'"):
        validate_workflow_definition_payload(stale)


def test_event_ports_are_typed_documents_and_match_paths_are_validated():
    derived = derive_workflow_port_schemas(
        _workflow({"document_type": "kanban_card"}, {}, []), BUILTIN_TYPES
    )
    properties = derived["graph"]["nodes"]["event"]["output_schema"]["properties"]
    assert set(properties) == {
        "event_id",
        "document_type",
        "key",
        "id",
        "op",
        "timestamp",
        "changed_fields",
        "before",
        "after",
    }
    assert properties["after"]["title"] == properties["before"]["title"] == "Kanban Card"

    bad = _workflow({"document_type": "kanban_card", "match": {"after.nope": 1}}, {}, [])
    with pytest.raises(ValueError, match="unknown path"):
        validate_workflow_definition_payload(derive_workflow_port_schemas(bad, BUILTIN_TYPES))


def test_match_path_requires_presence():
    node = WorkflowNodeEvent(document_type="kanban_card", match={"before.column_id": None})
    created = {"document_type": "kanban_card", "after": {"column_id": None}}
    assert not node.matches(created)
    assert node.matches({**created, "before": {"column_id": None}})


def test_document_write_rejects_non_writable_type():
    workflow = _workflow(
        {"document_type": "kanban_card"},
        {"write": {"kind": "document_write", "title": "Write", "document_type": "kanban_board"}},
        [],
    )
    with pytest.raises(ValueError, match="has no writable fields"):
        derive_workflow_port_schemas(workflow, BUILTIN_TYPES)


def test_document_write_derives_ports_from_writable_fields():
    workflow = _workflow(
        {"document_type": "kanban_card"},
        {"write": {"kind": "document_write", "title": "Write", "document_type": "session"}},
        [],
    )
    derived = derive_workflow_port_schemas(workflow, BUILTIN_TYPES)
    write = derived["graph"]["nodes"]["write"]
    assert write["input_schema"]["required"] == ["id"]
    assert set(write["input_schema"]["properties"]) == {"id", "title", "pinned", "archived"}
    assert set(write["output_schema"]["properties"]) == {"found", "title", "pinned", "archived"}
    assert write["output_schema"]["required"] == ["found"]


@pytest.mark.asyncio
async def test_document_write_node_executes_merge_and_sets_origin(store):
    workflow = _workflow(
        {"document_type": "session"},
        {
            "title_data": {
                "kind": "data",
                "title": "Title",
                "value": "Renamed",
                "output_schema": {"type": "string"},
            },
            "write": {
                "kind": "document_write",
                "title": "Set title",
                "document_type": "session",
            },
        },
        [
            ("event", "id", "write", "id"),
            ("title_data", "value", "write", "title"),
        ],
    )
    definition = save_workflow_definition(store, workflow)
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        f"{SESSION_KEY_PREFIX}t1",
        {
            "id": "t1",
            "thread_id": "t1",
            "source": "ui",
            "title": "Old",
            "first_message": "hi",
            "pinned": False,
            "archived": False,
            "created_at": "2024-01-01T00:00:00Z",
            "last_activity": "2024-01-01T00:00:00Z",
            "content": "",
        },
    )
    executor = ui_routes.get_workflow_executor()
    await executor.trigger_workflow(
        definition.id, build_manual_trigger_event(definition, payload={"id": "t1"})
    )
    [completed] = await _wait_for(lambda: _completed_runs(store, definition.id))
    assert completed.node_outputs["write"]["found"] is True
    assert completed.node_outputs["write"]["title"] == "Renamed"
    updated = store.get_document(PROGENITOR_STATE_PREFIX, f"{SESSION_KEY_PREFIX}t1")
    assert updated.payload["title"] == "Renamed"
    # Verify origin was set to run id
    events = store.read_document_events(EVENT_LOG_START, 100)
    session_events = [e for e in events if e.key == f"{SESSION_KEY_PREFIX}t1" and e.op == "updated"]
    assert len(session_events) == 1
    assert session_events[0].origin == completed.run_id


@pytest.mark.asyncio
async def test_document_write_node_not_found_returns_false(store):
    workflow = _workflow(
        {"document_type": "session"},
        {
            "title_data": {
                "kind": "data",
                "title": "Title",
                "value": "Renamed",
                "output_schema": {"type": "string"},
            },
            "write": {
                "kind": "document_write",
                "title": "Set title",
                "document_type": "session",
            },
        },
        [
            ("event", "id", "write", "id"),
            ("title_data", "value", "write", "title"),
        ],
    )
    definition = save_workflow_definition(store, workflow)
    executor = ui_routes.get_workflow_executor()
    await executor.trigger_workflow(
        definition.id, build_manual_trigger_event(definition, payload={"id": "missing"})
    )
    [completed] = await _wait_for(lambda: _completed_runs(store, definition.id))
    assert completed.node_outputs["write"]["found"] is False


@pytest.mark.asyncio
async def test_document_write_node_with_nothing_to_write_completes(store):
    workflow = _workflow(
        {"document_type": "session"},
        {"write": {"kind": "document_write", "title": "Set title", "document_type": "session"}},
        [("event", "id", "write", "id")],
    )
    definition = save_workflow_definition(store, workflow)
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        f"{SESSION_KEY_PREFIX}t1",
        {
            "id": "t1",
            "thread_id": "t1",
            "source": "ui",
            "title": "Old",
            "first_message": "hi",
            "pinned": False,
            "archived": False,
            "created_at": "2024-01-01T00:00:00Z",
            "last_activity": "2024-01-01T00:00:00Z",
            "content": "",
        },
    )
    executor = ui_routes.get_workflow_executor()
    await executor.trigger_workflow(
        definition.id, build_manual_trigger_event(definition, payload={"id": "t1"})
    )
    [completed] = await _wait_for(lambda: _completed_runs(store, definition.id))
    assert completed.node_outputs["write"] == {"found": True}


def test_in_memory_session_turn_updates_log_no_event_like_postgres():
    """Every turn rewrites content/last_activity; only real edits are events."""
    store = InMemoryDocumentStore()
    key = f"{SESSION_KEY_PREFIX}t-noise"
    session = {"id": "t-noise", "title": "New chat", "content": "", "last_activity": "t0"}
    store.save_document(PROGENITOR_STATE_PREFIX, key, session)
    store.merge_document(
        PROGENITOR_STATE_PREFIX, key, {"content": "<user> hi", "last_activity": "t1"}
    )
    store.save_document(
        PROGENITOR_STATE_PREFIX, key, {**session, "content": "x", "last_activity": "t2"}
    )
    store.mutate_document(PROGENITOR_STATE_PREFIX, key, lambda p: {**p, "last_activity": "t3"})
    store.merge_document(PROGENITOR_STATE_PREFIX, key, {"title": "Hiking", "last_activity": "t4"})

    events = [e for e in store.read_document_events(EVENT_LOG_START, 100) if e.key == key]
    assert [e.op for e in events] == ["created", "updated"]
    assert events[1].after["title"] == "Hiking"


def test_schema_driven_writable_fields_reject_readonly_values():
    session = BUILTIN_TYPES["session"]
    assert session.writable_fields == ("title", "pinned", "archived")
    assert session.validate_write({"title": "Renamed"}) is None
    assert "not writable" in (session.validate_write({"content": "no"}) or "")
    assert "not writable" in (session.validate_write({"missing": "no"}) or "")


def test_acp_task_schema_exposes_only_request_fields_as_writable():
    acp_task = BUILTIN_TYPES["acp_task"]
    assert acp_task.writable_fields == (
        "agent_name",
        "prompt",
        "cwd",
        "card_id",
        "status",
        "cancel_requested",
        "messages",
        "mode",
        "answers",
        "source",
        "request_metadata",
        "notify_on_completion",
    )
    assert acp_task.validate_write({"agent_name": "claude", "prompt": "build"}) is None
    assert "not writable" in (acp_task.validate_write({"session_id": "s"}) or "")


def test_document_create_generates_or_uses_id_and_rejects_duplicate(store):
    node = WorkflowNodeDocumentCreate(document_type="kanban_card")
    generated = _execute_document_create_node(
        store,
        node,
        {"title": "Generated", "summary": "", "column_id": "todo", "status": "idle"},
        "run-create",
    )
    assert len(generated["id"]) == 32
    assert generated["document"]["id"] == generated["id"]
    assert generated["document"]["created_at"] == generated["document"]["updated_at"]
    explicit = _execute_document_create_node(
        store,
        node,
        {
            "id": "explicit",
            "title": "Explicit",
            "summary": "",
            "column_id": "todo",
            "status": "idle",
        },
        "run-create",
    )
    assert explicit["key"] == "kanban_card:explicit"
    with pytest.raises(ValueError, match="already exists"):
        _execute_document_create_node(
            store,
            node,
            {
                "id": "explicit",
                "title": "Again",
                "summary": "",
                "column_id": "todo",
                "status": "idle",
            },
            "run-create",
        )
    with pytest.raises(ValueError, match="required property"):
        _execute_document_create_node(store, node, {"title": "Missing"}, "run-create")
    events = store.read_document_events(EVENT_LOG_START, 100)
    assert next(event for event in events if event.key == explicit["key"]).origin == "run-create"


def test_document_create_stamps_creation_times_by_field_type(store, monkeypatch):
    """`timestamp` and friends are always the real creation time: Unix seconds
    for a number field (alerts), ISO text otherwise."""
    import time

    from app.progenitor.document_types import DocumentType

    schema = {
        "type": "object",
        "required": ["id", "note", "timestamp", "created_at"],
        "properties": {
            "id": {"type": "string", "readOnly": True},
            "note": {"type": "string"},
            "timestamp": {"type": "number", "readOnly": True},
            "created_at": {"type": "string", "readOnly": True},
        },
    }
    monkeypatch.setattr(DocumentType, "load_schema", lambda self: schema)
    before = time.time()
    created = _execute_document_create_node(
        store, WorkflowNodeDocumentCreate(document_type="kanban_card"), {"note": "n"}, "run"
    )["document"]

    assert before <= created["timestamp"] <= time.time()
    assert created["created_at"].startswith(time.strftime("%Y-", time.gmtime()))


def test_document_create_alert_with_title_and_message_applies_defaults_and_timestamp(store):
    import time

    before = time.time()
    created = _execute_document_create_node(
        store,
        WorkflowNodeDocumentCreate(document_type="alert"),
        {"title": "Build complete", "message": "Everything passed."},
        "run-alert",
    )["document"]

    assert created["type"] == "info"
    assert created["urgent"] is False
    assert created["requires_response"] is False
    assert created["viewed"] is False
    assert before <= created["timestamp"] <= time.time()


def test_document_create_applies_and_copies_schema_defaults(store, monkeypatch):
    from app.progenitor.document_types import DocumentType

    schema = {
        "type": "object",
        "required": ["id", "title", "status", "metadata"],
        "properties": {
            "id": {"type": "string", "readOnly": True},
            "title": {"type": "string"},
            "status": {"type": "string", "default": "draft"},
            "metadata": {"type": "object", "default": {"labels": []}},
        },
    }
    monkeypatch.setattr(DocumentType, "load_schema", lambda self: schema)
    create = WorkflowNodeDocumentCreate(document_type="kanban_card")

    defaulted = _execute_document_create_node(store, create, {"title": "Default"}, "run")
    null_defaulted = _execute_document_create_node(
        store, create, {"title": "Null", "status": None}, "run"
    )
    overridden = _execute_document_create_node(
        store,
        create,
        {"title": "Override", "status": "published", "metadata": {"labels": ["x"]}},
        "run",
    )
    defaulted["document"]["metadata"]["labels"].append("changed")

    assert defaulted["document"]["status"] == "draft"
    assert null_defaulted["document"]["status"] == "draft"
    assert overridden["document"]["status"] == "published"
    assert overridden["document"]["metadata"] == {"labels": ["x"]}
    assert null_defaulted["document"]["metadata"] == {"labels": []}
    assert schema["properties"]["metadata"]["default"] == {"labels": []}


def test_document_write_never_applies_schema_defaults(store, monkeypatch):
    from app.progenitor.document_types import DocumentType

    schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "readOnly": True},
            "status": {"type": "string", "default": "draft"},
        },
    }
    monkeypatch.setattr(DocumentType, "load_schema", lambda self: schema)
    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:existing", {"id": "existing"})

    from app.backend.workflow_service import _execute_document_write_node

    result = _execute_document_write_node(
        store,
        WorkflowNodeDocumentWrite(document_type="kanban_card"),
        {"id": "existing", "status": None},
        "run",
    )

    assert result == {"found": True}
    assert store.get_document(PROGENITOR_STATE_PREFIX, "kanban_card:existing").payload == {
        "id": "existing"
    }


def test_document_delete_returns_before_and_tags_origin(store):
    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:gone", _card("gone", "todo"))
    node = WorkflowNodeDocumentDelete(document_type="kanban_card")
    deleted = _execute_document_delete_node(store, node, {"id": "gone"}, "run-delete")
    assert deleted == {"found": True, "before": _card("gone", "todo")}
    assert _execute_document_delete_node(store, node, {"id": "gone"}, "run-delete") == {
        "found": False,
        "before": None,
    }
    events = store.read_document_events(EVENT_LOG_START, 100)
    assert (
        next(
            event for event in events if event.key == "kanban_card:gone" and event.op == "deleted"
        ).origin
        == "run-delete"
    )


@pytest.mark.parametrize(
    ("op", "value", "expected"),
    [
        ("eq", "alpha", ["kanban_card:a"]),
        ("ne", "alpha", ["kanban_card:b", "kanban_card:c"]),
        ("lt", 3, ["kanban_card:a", "kanban_card:b"]),
        ("lte", 2, ["kanban_card:a", "kanban_card:b"]),
        ("gt", 1, ["kanban_card:b", "kanban_card:c"]),
        ("gte", 2, ["kanban_card:b", "kanban_card:c"]),
        ("in", ["alpha", "beta"], ["kanban_card:a", "kanban_card:b"]),
        ("contains", "ph", ["kanban_card:a"]),
        ("exists", True, ["kanban_card:a", "kanban_card:b", "kanban_card:c"]),
    ],
)
def test_document_query_operators(store, op, value, expected):
    store.save_document(
        PROGENITOR_STATE_PREFIX, "kanban_card:a", _card("a", "todo", title="alpha", position=1)
    )
    store.save_document(
        PROGENITOR_STATE_PREFIX, "kanban_card:b", _card("b", "todo", title="beta", position=2)
    )
    store.save_document(
        PROGENITOR_STATE_PREFIX, "kanban_card:c", _card("c", "todo", title="gamma", position=3)
    )
    field = "position" if op in {"lt", "lte", "gt", "gte"} else "title"
    node = WorkflowNodeDocumentQuery(
        document_type="kanban_card", filters=[{"field": field, "op": op}]
    )
    assert _execute_document_node(store, node, {field: value})["keys"] == expected


def test_document_query_datetime_order_total_and_unwired_filter(store):
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "kanban_card:a",
        _card("a", "todo", started_at="2024-01-02T00:00:00Z"),
    )
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "kanban_card:b",
        _card("b", "todo", started_at="2024-01-01T00:00:00Z"),
    )
    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:c", _card("c", "todo"))
    node = WorkflowNodeDocumentQuery(
        document_type="kanban_card",
        filters=[{"field": "started_at", "op": "gte"}],
        order_by="started_at",
        order="desc",
        limit=2,
    )
    output = _execute_document_node(store, node, {"started_at": "2024-01-01T12:00:00Z"})
    assert output["keys"] == ["kanban_card:a"]
    assert output["count"] == 1 and output["total"] == 1
    unwired = _execute_document_node(store, node, {})
    assert unwired["keys"] == ["kanban_card:a", "kanban_card:b"]
    assert unwired["count"] == 2 and unwired["total"] == 3


@pytest.mark.asyncio
async def test_card_documents_written_by_any_writer_keep_the_board_order(store):
    """A card created or deleted outside the card routes (e.g. by a workflow
    document node) appears on, and leaves, the board."""
    from app.backend.document_store import document_write_origin

    dispatcher = ui_routes.get_document_event_dispatcher()
    store.save_document(
        PROGENITOR_STATE_PREFIX, "kanban_board", {"columns": [], "card_order": ["old"]}
    )
    with document_write_origin("workflow_run_x"):
        store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:new", _card("new", "plan"))
    await dispatcher.drain()

    board = store.get_document(PROGENITOR_STATE_PREFIX, "kanban_board")
    assert board.payload["card_order"] == ["old", "new"]
    board_events = [
        e for e in store.read_document_events(EVENT_LOG_START, 100) if e.key == "kanban_board"
    ]
    assert board_events[-1].origin == "workflow_run_x"

    store.delete_document(PROGENITOR_STATE_PREFIX, "kanban_card:new")
    await dispatcher.drain()
    board = store.get_document(PROGENITOR_STATE_PREFIX, "kanban_board")
    assert board.payload["card_order"] == ["old"]
