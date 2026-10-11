import asyncio

import pytest

from app.backend import ui_routes
from app.backend.document_events import (
    DocumentEventDispatcher,
    TypedDocumentEvent,
    seed_document_types,
)
from app.backend.document_store import (
    PROGENITOR_STATE_PREFIX,
    InMemoryDocumentStore,
    document_write_origin,
    set_default_document_store,
)
from app.backend.workflow_service import (
    build_manual_trigger_event,
    list_workflow_runs,
    save_workflow_definition,
)
from app.progenitor.document_types import BUILTIN_DOCUMENT_TYPES
from app.progenitor.workflows import apply_document_type_schemas, default_workflow_definition

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

    events = store.read_document_events(0, 10)
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
    assert {"after.title", "before.column_id", "after"} <= set(
        definition.graph.nodes["event"].output_schema["properties"]
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
    assert run.trigger_event["before.column_id"] == "build"
    assert run.trigger_event["changed_fields"] == ["column_id"]
    assert run.node_outputs["body"]["found"] is True
    assert run.node_outputs["body"]["document.content"] == "# Brief"


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
async def test_query_node_returns_collection_documents_matching_event_field(store):
    workflow = _workflow(
        {"document_type": "kanban_card", "operations": ["created"]},
        {
            "peers": {
                "kind": "document_query",
                "title": "Same column",
                "document_type": "kanban_card",
                "filter_fields": ["column_id"],
            }
        },
        [("event", "after.column_id", "peers", "column_id")],
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
    assert runs[0].node_outputs["peers"]["keys"] == ["kanban_card:a", "kanban_card:c"]
    assert runs[0].node_outputs["peers"]["count"] == 2


def test_document_node_types_are_validated_against_the_registry():
    unknown = _workflow({"document_type": "nope"}, {}, [])
    with pytest.raises(ValueError, match="unknown document type 'nope'"):
        apply_document_type_schemas(unknown, BUILTIN_TYPES)

    single = _workflow(
        {"document_type": "kanban_card"},
        {"q": {"kind": "document_query", "title": "Q", "document_type": "preferences"}},
        [],
    )
    with pytest.raises(ValueError, match="needs a collection document type"):
        apply_document_type_schemas(single, BUILTIN_TYPES)
