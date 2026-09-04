import pytest

from app.progenitor.workflows import (
    WorkflowDefinition,
    default_workflow_definition,
    ordered_workflow_node_ids,
    validate_workflow_definition_payload,
)


def _node_id(payload, kind):
    return next(
        node_id for node_id, node in payload["graph"]["nodes"].items() if node["kind"] == kind
    )


def _agent_node(title, *, message="Handle it.", input_schema=None, output_schema=None):
    return {
        "kind": "agent",
        "title": title,
        "message": message,
        "input_schema": input_schema or {"type": "object", "properties": {}},
        "output_schema": output_schema or {"type": "object", "properties": {}},
    }


def test_default_workflow_wires_an_event_property():
    normalized = validate_workflow_definition_payload(default_workflow_definition())
    event_id = _node_id(normalized, "event")
    agent_id = _node_id(normalized, "agent")

    event = normalized["graph"]["nodes"][event_id]
    agent = normalized["graph"]["nodes"][agent_id]
    assert "thread_id" not in event["output_schema"]["properties"]
    assert agent["input_schema"]["properties"]["event_type"] == {"type": "string"}
    assert normalized["graph"]["edges"] == [
        {
            "from": {"node": event_id, "port": "event_type"},
            "to": {"node": agent_id, "port": "event_type"},
        }
    ]


def test_workflow_edges_have_no_control_kind():
    payload = default_workflow_definition()
    payload["graph"]["edges"][0]["kind"] = "control"

    with pytest.raises(ValueError, match="schema validation failed"):
        validate_workflow_definition_payload(payload)


def test_connected_property_is_the_execution_dependency():
    payload = default_workflow_definition()
    event_id = _node_id(payload, "event")
    source_id = _node_id(payload, "agent")
    target_id = "agent_consumer"
    payload["graph"]["nodes"][source_id] = _agent_node(
        "Source",
        input_schema={
            "type": "object",
            "required": ["event_type"],
            "properties": {"event_type": {"type": "string"}},
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
            "from": {"node": event_id, "port": "event_type"},
            "to": {"node": source_id, "port": "event_type"},
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
    payload = default_workflow_definition()
    agent_id = _node_id(payload, "agent")
    payload["graph"]["nodes"][agent_id]["input_schema"] = {
        "type": "object",
        "required": ["card"],
        "properties": {"card": {"type": "object"}, "event_type": {"type": "string"}},
    }
    event_id = _node_id(payload, "event")
    payload["graph"]["edges"] = [
        {
            "from": {"node": event_id, "port": "event_type"},
            "to": {"node": agent_id, "port": "event_type"},
        }
    ]

    with pytest.raises(ValueError, match="required input 'card'"):
        validate_workflow_definition_payload(payload)


def test_duplicate_input_connection_is_rejected():
    payload = default_workflow_definition()
    agent_id = _node_id(payload, "agent")
    data_id = "data_event_type"
    payload["graph"]["nodes"][data_id] = {
        "kind": "data",
        "title": "Event type",
        "value": "manual",
        "output_schema": {"type": "string"},
    }
    payload["graph"]["edges"].append(
        {"from": {"node": data_id, "port": "value"}, "to": {"node": agent_id, "port": "event_type"}}
    )

    with pytest.raises(ValueError, match="more than one edge"):
        validate_workflow_definition_payload(payload)


def test_cycle_of_connected_properties_is_rejected():
    payload = default_workflow_definition()
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
            "from": {"node": event_id, "port": "event_type"},
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
