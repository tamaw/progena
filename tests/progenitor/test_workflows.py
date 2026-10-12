import pytest

from app.progenitor.document_types import BUILTIN_DOCUMENT_TYPES
from app.progenitor.workflows import (
    WorkflowDefinition,
    default_workflow_definition,
    derive_workflow_port_schemas,
    ordered_workflow_node_ids,
    validate_workflow_definition_payload,
)


def _node_id(payload, kind):
    return next(
        node_id for node_id, node in payload["graph"]["nodes"].items() if node["kind"] == kind
    )


def _agent_node(title, *, template="Handle it.", input_schema=None, output_schema=None):
    return {
        "kind": "agent",
        "title": title,
        "template": template,
        "input_schema": input_schema or {"type": "object", "properties": {}},
        "output_schema": output_schema or {"type": "object", "properties": {}},
    }


def _default_with_schemas():
    return derive_workflow_port_schemas(
        default_workflow_definition(), {t.id: t for t in BUILTIN_DOCUMENT_TYPES}
    )


def test_default_workflow_wires_an_event_property():
    normalized = validate_workflow_definition_payload(_default_with_schemas())
    event_id = _node_id(normalized, "event")
    agent_id = _node_id(normalized, "agent")

    event = normalized["graph"]["nodes"][event_id]
    agent = normalized["graph"]["nodes"][agent_id]
    assert "thread_id" not in event["output_schema"]["properties"]
    assert agent["input_schema"]["properties"]["key"] == {}
    assert normalized["graph"]["edges"] == [
        {
            "from": {"node": event_id, "port": "key"},
            "to": {"node": agent_id, "port": "key"},
        }
    ]


def test_workflow_edges_have_no_control_kind():
    payload = default_workflow_definition()
    payload["graph"]["edges"][0]["kind"] = "control"

    with pytest.raises(ValueError, match="schema validation failed"):
        validate_workflow_definition_payload(payload)


def test_connected_property_is_the_execution_dependency():
    payload = _default_with_schemas()
    event_id = _node_id(payload, "event")
    source_id = _node_id(payload, "agent")
    target_id = "agent_consumer"
    payload["graph"]["nodes"][source_id] = _agent_node(
        "Source",
        input_schema={
            "type": "object",
            "required": ["key"],
            "properties": {"key": {"type": "string"}},
        },
        output_schema={
            "type": "object",
            "required": ["title"],
            "properties": {"title": {"type": "string"}},
        },
    )
    payload["graph"]["nodes"][target_id] = _agent_node(
        "Consumer",
        input_schema={
            "type": "object",
            "required": ["title"],
            "properties": {"title": {"type": "string"}},
        },
    )
    payload["graph"]["edges"] = [
        {
            "from": {"node": event_id, "port": "key"},
            "to": {"node": source_id, "port": "key"},
        },
        {"from": {"node": source_id, "port": "title"}, "to": {"node": target_id, "port": "title"}},
    ]

    normalized = validate_workflow_definition_payload(payload)
    assert ordered_workflow_node_ids(WorkflowDefinition.model_validate(normalized)) == [
        source_id,
        target_id,
    ]


def test_executable_node_without_connection_is_rejected():
    payload = default_workflow_definition()
    payload["graph"]["edges"] = []

    with pytest.raises(ValueError, match="no inbound connection"):
        validate_workflow_definition_payload(payload)


def test_required_input_must_have_a_connected_property():
    payload = _default_with_schemas()
    agent_id = _node_id(payload, "agent")
    payload["graph"]["nodes"][agent_id]["input_schema"] = {
        "type": "object",
        "required": ["after"],
        "properties": {"after": {"type": "object"}, "key": {"type": "string"}},
    }
    event_id = _node_id(payload, "event")
    payload["graph"]["edges"] = [
        {
            "from": {"node": event_id, "port": "key"},
            "to": {"node": agent_id, "port": "key"},
        }
    ]

    with pytest.raises(ValueError, match="required input 'after'"):
        validate_workflow_definition_payload(payload)


def test_duplicate_input_connection_is_rejected():
    payload = _default_with_schemas()
    agent_id = _node_id(payload, "agent")
    data_id = "data_key"
    payload["graph"]["nodes"][data_id] = {
        "kind": "data",
        "title": "Key",
        "value": "manual",
        "output_schema": {"type": "string"},
    }
    payload["graph"]["edges"].append(
        {"from": {"node": data_id, "port": "value"}, "to": {"node": agent_id, "port": "key"}}
    )

    with pytest.raises(ValueError, match="more than one edge"):
        validate_workflow_definition_payload(payload)


def test_cycle_of_connected_properties_is_rejected():
    payload = _default_with_schemas()
    event_id = _node_id(payload, "event")
    first_id = _node_id(payload, "agent")
    second_id = "agent_second"
    schema = {"type": "object", "required": ["value"], "properties": {"value": {"type": "string"}}}
    output = {"type": "object", "properties": {"value": {"type": "string"}}}
    payload["graph"]["nodes"][first_id] = _agent_node(
        "First", input_schema=schema, output_schema=output
    )
    payload["graph"]["nodes"][second_id] = _agent_node(
        "Second", input_schema=schema, output_schema=output
    )
    payload["graph"]["edges"] = [
        {
            "from": {"node": event_id, "port": "key"},
            "to": {"node": first_id, "port": "value"},
        },
        {"from": {"node": first_id, "port": "value"}, "to": {"node": second_id, "port": "value"}},
        {"from": {"node": second_id, "port": "value"}, "to": {"node": first_id, "port": "value"}},
    ]

    with pytest.raises(ValueError, match="more than one edge|acyclic"):
        validate_workflow_definition_payload(payload)


def test_condition_and_approval_expose_selected_value_ports():
    payload = default_workflow_definition()
    agent_id = _node_id(payload, "agent")
    condition_id = "condition_gate"
    payload["graph"]["nodes"][condition_id] = {
        "kind": "condition",
        "title": "Gate",
        "expression": "ready",
        "input_schema": {
            "type": "object",
            "required": ["ready"],
            "properties": {"ready": {"type": "boolean"}},
        },
    }
    payload["graph"]["nodes"][agent_id] = _agent_node(
        "Consumer",
        input_schema={
            "type": "object",
            "required": ["result"],
            "properties": {"result": {"type": "boolean"}},
        },
    )
    payload["graph"]["nodes"]["data_ready"] = {
        "kind": "data",
        "title": "Ready",
        "value": True,
        "output_schema": {"type": "boolean"},
    }
    payload["graph"]["edges"] = [
        {
            "from": {"node": "data_ready", "port": "value"},
            "to": {"node": condition_id, "port": "ready"},
        },
        {
            "from": {"node": condition_id, "port": "true"},
            "to": {"node": agent_id, "port": "result"},
        },
    ]

    normalized = validate_workflow_definition_payload(payload)
    assert normalized["graph"]["nodes"][condition_id]["output_schema"]["properties"]["true"] == {
        "type": "boolean"
    }


def test_derive_extract_ports_from_inline_template():
    payload = default_workflow_definition()
    agent_id = next(nid for nid, n in payload["graph"]["nodes"].items() if n["kind"] == "agent")
    event_id = next(nid for nid, n in payload["graph"]["nodes"].items() if n["kind"] == "event")
    extract_id = "extract_1"
    payload["graph"]["nodes"][agent_id]["template"] = "Consume: {result}"
    payload["graph"]["nodes"][extract_id] = {
        "kind": "extract",
        "title": "Extract",
        "template": "---\ninputs:\n  title:\n    type: string\noutput_schema:\n  type: string\n---\ntitle: {title}",
    }
    payload["graph"]["edges"] = [
        {
            "from": {"node": event_id, "port": "key"},
            "to": {"node": extract_id, "port": "title"},
        },
        {
            "from": {"node": extract_id, "port": "result"},
            "to": {"node": agent_id, "port": "result"},
        },
    ]
    typed = derive_workflow_port_schemas(payload, {t.id: t for t in BUILTIN_DOCUMENT_TYPES})
    normalized = validate_workflow_definition_payload(typed)
    extract = normalized["graph"]["nodes"][extract_id]
    assert extract["input_schema"]["required"] == ["title"]
    assert extract["input_schema"]["properties"]["title"] == {"type": "string"}
    assert extract["output_schema"]["properties"]["result"] == {"type": "string"}


def test_derive_extract_ports_from_prompt_document():
    def fake_loader(path: str) -> str:
        return "---\ninputs:\n  name:\n    type: string\noutput_schema:\n  type: string\n---\nname: {name}"

    payload = default_workflow_definition()
    extract_id = "extract_1"
    payload["graph"]["nodes"][extract_id] = {
        "kind": "extract",
        "title": "Extract",
        "prompt": "workflows/test.md",
    }
    agent_id = next(nid for nid, n in payload["graph"]["nodes"].items() if n["kind"] == "agent")
    payload["graph"]["nodes"][agent_id]["template"] = "Hello {name}"
    payload["graph"]["edges"] = [
        {
            "from": {"node": extract_id, "port": "result"},
            "to": {"node": agent_id, "port": "name"},
        }
    ]
    typed = derive_workflow_port_schemas(
        payload,
        {t.id: t for t in BUILTIN_DOCUMENT_TYPES},
        prompt_loader=fake_loader,
    )
    extract = typed["graph"]["nodes"][extract_id]
    assert extract["input_schema"]["required"] == ["name"]
    assert extract["input_schema"]["properties"]["name"] == {"type": "string"}
    assert extract["output_schema"]["properties"]["result"] == {"type": "string"}


def test_extract_schema_precedence_node_over_frontmatter():
    payload = default_workflow_definition()
    extract_id = "extract_1"
    payload["graph"]["nodes"][extract_id] = {
        "kind": "extract",
        "title": "Extract",
        "template": "---\noutput_schema:\n  type: string\n---\nHello",
        "schema": {"type": "number"},
    }
    typed = derive_workflow_port_schemas(payload, {t.id: t for t in BUILTIN_DOCUMENT_TYPES})
    extract = typed["graph"]["nodes"][extract_id]
    assert extract["output_schema"]["properties"]["result"] == {"type": "number"}


def test_extract_schema_precedence_frontmatter_over_downstream():
    payload = default_workflow_definition()
    extract_id = "extract_1"
    agent_id = next(nid for nid, n in payload["graph"]["nodes"].items() if n["kind"] == "agent")
    payload["graph"]["nodes"][extract_id] = {
        "kind": "extract",
        "title": "Extract",
        "template": "---\noutput_schema:\n  type: number\n---\nHello",
    }
    payload["graph"]["nodes"][agent_id]["template"] = "Result: {result}"
    payload["graph"]["edges"] = [
        {
            "from": {"node": extract_id, "port": "result"},
            "to": {"node": agent_id, "port": "result"},
        }
    ]
    typed = derive_workflow_port_schemas(payload, {t.id: t for t in BUILTIN_DOCUMENT_TYPES})
    extract = typed["graph"]["nodes"][extract_id]
    assert extract["output_schema"]["properties"]["result"] == {"type": "number"}


def _extract_feeding(targets):
    """Default workflow plus an Extract whose ``result`` feeds each (node_id, port, node)."""

    payload = default_workflow_definition()
    payload["graph"]["nodes"]["extract_1"] = {
        "kind": "extract",
        "title": "Extract",
        "template": "Pick a column.",
    }
    payload["graph"]["edges"] = []
    for node_id, port, node in targets:
        payload["graph"]["nodes"][node_id] = node
        payload["graph"]["edges"].append(
            {"from": {"node": "extract_1", "port": "result"}, "to": {"node": node_id, "port": port}}
        )
    return payload


_COLUMN_QUERY = {
    "kind": "document_query",
    "title": "Cards in column",
    "document_type": "kanban_card",
    "filter_fields": ["column_id"],
}
_TYPED_CARD_AGENT = _agent_node(
    "Card agent", template="---\ninputs:\n  card: kanban_card\n---\nLook at {card.title}."
)
_TYPES = {t.id: t for t in BUILTIN_DOCUMENT_TYPES}


def test_extract_schema_inferred_from_typed_downstream_input():
    typed = derive_workflow_port_schemas(
        _extract_feeding([("peers", "column_id", _COLUMN_QUERY)]), _TYPES
    )
    peers_input = typed["graph"]["nodes"]["peers"]["input_schema"]["properties"]["column_id"]
    result = typed["graph"]["nodes"]["extract_1"]["output_schema"]["properties"]["result"]
    assert result == peers_input
    assert result["type"] == "string"


def test_extract_schema_untyped_downstream_input_is_not_a_schema():
    untyped_agent = _agent_node("Plain", template="Value: {result}")
    with pytest.raises(ValueError, match="needs a schema"):
        derive_workflow_port_schemas(_extract_feeding([("plain", "result", untyped_agent)]), _TYPES)


def test_extract_schema_conflicting_targets_rejected():
    payload = _extract_feeding(
        [("peers", "column_id", _COLUMN_QUERY), ("card_agent", "card", _TYPED_CARD_AGENT)]
    )
    with pytest.raises(ValueError, match="different schemas"):
        derive_workflow_port_schemas(payload, _TYPES)


def test_unpack_of_extract_result_sees_the_resolved_schema():
    payload = _extract_feeding([("card", "value", {"kind": "unpack", "title": "Card"})])
    payload["graph"]["nodes"]["extract_1"]["schema"] = "kanban_card"
    typed = derive_workflow_port_schemas(payload, _TYPES)
    unpack = typed["graph"]["nodes"]["card"]
    assert unpack["input_schema"]["properties"]["value"]["title"] == "Kanban Card"
    assert {"column_id", "title"} <= set(unpack["output_schema"]["properties"])


def test_extract_schema_no_targets_rejected():
    payload = default_workflow_definition()
    extract_id = "extract_1"
    payload["graph"]["nodes"][extract_id] = {
        "kind": "extract",
        "title": "Extract",
        "template": "Hello",
    }
    with pytest.raises(ValueError, match="needs a schema"):
        derive_workflow_port_schemas(payload, {t.id: t for t in BUILTIN_DOCUMENT_TYPES})


def cron_workflow(*, expression="0 9 * * 1-5", timezone="Australia/Melbourne", overlap="skip"):
    """Cron trigger feeding ``scheduled_for`` into an agent template."""

    payload = default_workflow_definition()
    payload["graph"] = {
        "nodes": {
            "cron": {
                "kind": "cron",
                "title": "Weekdays",
                "expression": expression,
                "timezone": timezone,
                "overlap": overlap,
            },
            "agent": _agent_node("Digest", template="Due at {scheduled_for}"),
        },
        "edges": [
            {
                "from": {"node": "cron", "port": "scheduled_for"},
                "to": {"node": "agent", "port": "scheduled_for"},
            }
        ],
    }
    payload["ui"] = {}
    return payload


def _derive(payload):
    return derive_workflow_port_schemas(payload, {t.id: t for t in BUILTIN_DOCUMENT_TYPES})


def test_cron_only_workflow_is_valid_and_exposes_typed_ports():
    normalized = validate_workflow_definition_payload(_derive(cron_workflow()))
    ports = normalized["graph"]["nodes"]["cron"]["output_schema"]["properties"]
    assert ports["scheduled_for"] == {"type": "string", "format": "date-time"}
    assert set(ports) == {"scheduled_for", "fired_at", "timezone"}


@pytest.mark.parametrize(
    ("expression", "timezone", "message"),
    [
        ("0 9 * *", "UTC", "must have 5 fields"),
        ("0 25 * * *", "UTC", "invalid cron expression"),
        ("*/5 * * * * *", "UTC", "must have 5 fields"),
        ("0 9 * * *", "Mars/Olympus", "unknown timezone"),
    ],
)
def test_cron_node_rejects_bad_expression_or_timezone(expression, timezone, message):
    with pytest.raises(ValueError, match=message):
        validate_workflow_definition_payload(
            _derive(cron_workflow(expression=expression, timezone=timezone))
        )


def test_cron_node_cannot_have_inbound_edges():
    payload = cron_workflow()
    payload["graph"]["nodes"]["data"] = {
        "kind": "data",
        "title": "Data",
        "value": "x",
        "output_schema": {"type": "string"},
    }
    payload["graph"]["edges"].append(
        {"from": {"node": "data", "port": "value"}, "to": {"node": "cron", "port": "timezone"}}
    )
    with pytest.raises(ValueError, match="no input port|cannot have inbound"):
        validate_workflow_definition_payload(_derive(payload))


def test_workflow_without_any_trigger_is_rejected():
    payload = cron_workflow()
    del payload["graph"]["nodes"]["cron"]
    payload["graph"]["edges"] = []
    with pytest.raises(ValueError, match="at least one event or cron trigger"):
        validate_workflow_definition_payload(_derive(payload))
