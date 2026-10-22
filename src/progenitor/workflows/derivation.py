"""Derive authoritative workflow ports from document types and templates."""

from __future__ import annotations

import contextlib
import copy
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from src.progenitor.documents.schema import _SCHEMA_DEFINITION_KEYS, typed_document_schema
from src.progenitor.documents.types import DocumentType
from src.progenitor.instructions.templates import (
    parse_format_template,
    parse_instruction_template,
    resolve_type_ref,
)
from src.progenitor.workflows.constants import _ANY_VALUE_SCHEMA, CRON_OUTPUT_SCHEMA, RoutineLoader
from src.progenitor.workflows.models import DocumentQueryFilter
from src.progenitor.workflows.ports import (
    _derive_agent_output_schema,
    document_create_input_schema,
    document_create_output_schema,
    document_delete_input_schema,
    document_delete_output_schema,
    document_event_output_schema,
    document_query_input_schema,
    document_query_output_schema,
    document_read_input_schema,
    document_read_output_schema,
    document_write_input_schema,
    document_write_output_schema,
    routine_node_port_schemas,
)
from src.progenitor.workflows.schema_values import (
    _unpack_input_schema,
    _unpack_output_schema,
    schema_at_path,
    unpackable_properties,
)


def _node_instruction_text(
    node: dict[str, Any], instruction_loader: Callable[[str], str] | None
) -> str:
    """Return the instruction template text for an extract/agent node."""
    instruction_path = node.get("instruction")
    if instruction_path:
        if instruction_loader is None:
            raise ValueError(
                f"node '{node.get('title')}' references instruction '{instruction_path}' but "
                f"no instruction loader is available"
            )
        return instruction_loader(instruction_path)
    template = node.get("template")
    if isinstance(template, str):
        return template
    raise ValueError("extract/agent node must have either 'instruction' or 'template'")


def _derive_prompt_node_input_schema(
    node: dict[str, Any],
    document_types: Mapping[str, DocumentType],
    instruction_loader: Callable[[str], str] | None,
) -> dict[str, Any]:
    """Derive input schema for an extract/agent node from its prompt template."""
    text = _node_instruction_text(node, instruction_loader)
    template = parse_instruction_template(text)
    properties: dict[str, Any] = {}
    definitions: dict[str, dict[str, Any]] = {}
    for root in template.roots:
        type_ref = template.inputs.get(root)
        if type_ref is not None:
            try:
                properties[root], root_definitions = resolve_type_ref(type_ref, document_types)
            except ValueError as exc:
                raise ValueError(f"front matter inputs '{root}': {exc}") from None
            _merge_definitions(definitions, root_definitions)
        else:
            properties[root] = _ANY_VALUE_SCHEMA
    properties["thread_id"] = {"type": "string"}
    required = list(template.roots)
    return {"type": "object", "required": required, "properties": properties, **definitions}


def _derive_format_node_input_schema(node: dict[str, Any]) -> dict[str, Any]:
    """Derive one required untyped input port per format placeholder root."""

    template = parse_format_template(str(node.get("template") or ""))
    roots: list[str] = []
    for placeholder in template.placeholders:
        root = placeholder.split(".", 1)[0]
        if root not in roots:
            roots.append(root)
    return {
        "type": "object",
        "required": roots,
        "properties": {root: {} for root in roots},
    }


def _derive_format_node_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["text"],
        "properties": {"text": {"type": "string"}},
    }


def _merge_definitions(
    target: dict[str, dict[str, Any]], source: Mapping[str, Mapping[str, Any]]
) -> None:
    """Merge root definition maps; one name with two different schemas is an error."""

    for key, defs in source.items():
        bucket = target.setdefault(key, {})
        for name, schema in defs.items():
            if name in bucket and bucket[name] != schema:
                raise ValueError(f"conflicting schema definitions for '{key}/{name}'")
            bucket[name] = schema


def _extract_result_schema(
    result_schema: dict[str, Any], definitions: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["result", "thread_id"],
        "properties": {
            "result": result_schema,
            "thread_id": {"type": "string"},
        },
        **definitions,
    }


def _derive_extract_output_schema(
    node: dict[str, Any],
    node_id: str,
    nodes: dict[str, Any],
    edges: list[Any],
    document_types: Mapping[str, DocumentType],
    instruction_loader: Callable[[str], str] | None,
) -> dict[str, Any]:
    """Derive the result schema for an extract node with precedence rules."""
    # 1. Node-level schema wins
    node_schema = node.get("schema")
    if node_schema is not None:
        return _extract_result_schema(*resolve_type_ref(node_schema, document_types))

    # 2. Template front matter output_schema
    text = _node_instruction_text(node, instruction_loader)
    template = parse_instruction_template(text)
    if template.output_schema is not None:
        try:
            resolved = resolve_type_ref(template.output_schema, document_types)
        except ValueError as exc:
            raise ValueError(f"front matter output_schema: {exc}") from None
        return _extract_result_schema(*resolved)

    # 3. Infer from downstream non-unpack targets
    target_schemas: list[dict[str, Any]] = []
    target_definitions: dict[str, dict[str, Any]] = {}
    for edge in edges:
        source = edge.get("from") if isinstance(edge, dict) else None
        target = edge.get("to") if isinstance(edge, dict) else None
        if (
            isinstance(source, dict)
            and isinstance(target, dict)
            and source.get("node") == node_id
            and source.get("port") == "result"
        ):
            target_node = nodes.get(str(target.get("node")))
            if not isinstance(target_node, dict) or target_node.get("kind") == "unpack":
                continue
            target_input = (
                target_node.get("input_schema")
                if isinstance(target_node, dict)
                else target_node.input_schema
            )
            target_schema = schema_at_path(
                target_input.get("properties", {}) if isinstance(target_input, Mapping) else {},
                str(target.get("port")),
            )
            # An untyped input ({}: e.g. an Agent placeholder) carries no shape
            # to ask Progenitor for, so it never counts as an inferred schema.
            if target_schema:
                target_schemas.append(target_schema)
                for key in _SCHEMA_DEFINITION_KEYS:
                    defs = target_input.get(key) if isinstance(target_input, Mapping) else None
                    if isinstance(defs, dict):
                        _merge_definitions(target_definitions, {key: defs})

    if not target_schemas:
        raise ValueError(f"extract node '{node_id}' needs a schema")

    first = target_schemas[0]
    for other in target_schemas[1:]:
        if other != first:
            raise ValueError(
                f"extract node '{node_id}' result is wired to multiple targets with "
                f"different schemas"
            )

    return {
        "type": "object",
        "required": ["result", "thread_id"],
        "properties": {
            "result": first,
            "thread_id": {"type": "string"},
        },
        **target_definitions,
    }


def derive_workflow_port_schemas(
    payload: Mapping[str, Any],
    document_types: Mapping[str, DocumentType],
    instruction_loader: Callable[[str], str] | None = None,
    routine_loader: RoutineLoader | None = None,
) -> dict[str, Any]:
    """Return ``payload`` with every derived node's port schemas recomputed.

    Cron nodes get the fixed cron output schema. Document nodes take their
    schemas from the registered type schema; Unpack nodes take theirs from the
    port feeding their ``value``
    input, so Unpack chains resolve in dependency order. Extract/agent nodes
    take theirs from their prompt template placeholders, routine nodes from
    the routine graph. Run on every save path so ports never reflect whatever
    schemas the writer sent.
    """

    result = copy.deepcopy(dict(payload))
    graph = result.get("graph")
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    if not isinstance(nodes, dict):
        return result
    schemas: dict[str, dict[str, Any] | None] = {}
    for node_id, node in nodes.items():
        if isinstance(node, dict) and node.get("kind") == "cron":
            node["output_schema"] = copy.deepcopy(CRON_OUTPUT_SCHEMA)
            continue
        if not isinstance(node, dict) or node.get("kind") not in {
            "event",
            "document_read",
            "document_query",
            "document_write",
            "document_create",
            "document_delete",
        }:
            continue
        type_id = node.get("document_type")
        document_type = document_types.get(type_id) if isinstance(type_id, str) else None
        if document_type is None:
            known = ", ".join(sorted(document_types)) or "none registered"
            raise ValueError(
                f"node '{node_id}' references unknown document type '{type_id}' (known: {known})"
            )
        if document_type.id not in schemas:
            schemas[document_type.id] = document_type.load_schema()
        document_schema = schemas[document_type.id]
        kind = node["kind"]
        if kind == "event":
            node["output_schema"] = document_event_output_schema(document_type, document_schema)
        elif kind == "document_read":
            node["input_schema"] = document_read_input_schema(document_type)
            node["output_schema"] = document_read_output_schema(document_type, document_schema)
        elif kind == "document_write":
            if not document_type.writable_fields:
                raise ValueError(
                    f"document_write node '{node_id}' cannot use '{document_type.id}': "
                    f"it has no writable fields"
                )
            node["input_schema"] = document_write_input_schema(document_type, document_schema)
            node["output_schema"] = document_write_output_schema(document_type, document_schema)
        elif kind == "document_create":
            if not document_type.collection:
                raise ValueError(
                    f"document_create node '{node_id}' needs a collection document type, "
                    f"not single document '{document_type.id}'"
                )
            if document_schema is None:
                raise ValueError(
                    f"document_create node '{node_id}' needs document type '{document_type.id}' "
                    "to have a schema"
                )
            node["input_schema"] = document_create_input_schema(document_type, document_schema)
            node["output_schema"] = document_create_output_schema(document_type, document_schema)
        elif kind == "document_delete":
            if not document_type.collection:
                raise ValueError(
                    f"document_delete node '{node_id}' needs a collection document type, "
                    f"not single document '{document_type.id}'"
                )
            node["input_schema"] = document_delete_input_schema()
            node["output_schema"] = document_delete_output_schema(document_type, document_schema)
        else:
            if not document_type.collection:
                raise ValueError(
                    f"document_query node '{node_id}' needs a collection document type, "
                    f"not single document '{document_type.id}'"
                )
            raw_filters = node.get("filters", [])
            if not isinstance(raw_filters, list):
                raise ValueError(f"document_query node '{node_id}' filters must be a list")
            try:
                filters = [DocumentQueryFilter.model_validate(item) for item in raw_filters]
            except Exception as exc:
                raise ValueError(f"document_query node '{node_id}' has an invalid filter") from exc
            order_by = node.get("order_by")
            if order_by is not None and (
                not isinstance(order_by, str)
                or order_by
                not in unpackable_properties(
                    typed_document_schema(document_type, document_schema)[0]
                )
            ):
                raise ValueError(
                    f"document_query node '{node_id}' has no order_by field '{order_by}'"
                )
            if node.get("order", "asc") not in {"asc", "desc"}:
                raise ValueError(f"document_query node '{node_id}' order must be 'asc' or 'desc'")
            node["input_schema"] = document_query_input_schema(
                document_type, document_schema, filters
            )
            node["output_schema"] = document_query_output_schema(document_type, document_schema)
    # Before prompt nodes: an Extract may infer its result from a routine input.
    for node_id, node in nodes.items():
        if not isinstance(node, dict) or node.get("kind") != "routine":
            continue
        routine_id = node.get("routine")
        routine_graph = (
            routine_loader(routine_id)
            if routine_loader is not None and isinstance(routine_id, str)
            else None
        )
        if not isinstance(routine_id, str) or routine_graph is None:
            raise ValueError(f"routine node '{node_id}' references unknown routine '{routine_id}'")
        node["input_schema"], node["output_schema"] = routine_node_port_schemas(
            routine_id, routine_graph
        )
    raw_edges = graph.get("edges") if isinstance(graph, dict) else None
    edges: list[Any] = raw_edges if isinstance(raw_edges, list) else []
    format_nodes = {
        node_id: node
        for node_id, node in nodes.items()
        if isinstance(node, dict) and node.get("kind") == "format"
    }
    for node in format_nodes.values():
        node["input_schema"] = _derive_format_node_input_schema(node)
        node["output_schema"] = _derive_format_node_output_schema()
    prompt_nodes = {
        node_id: node
        for node_id, node in nodes.items()
        if isinstance(node, dict) and node.get("kind") in {"extract", "agent"}
    }
    # Order matters: every prompt node's inputs first (Extract infers its result
    # from the inputs it feeds), then Extract results, then Unpack (which may
    # unpack an Extract result; an Unpack value never constrains an Extract).
    for node_id, node in prompt_nodes.items():
        with _prompt_node_context(node_id, node):
            node["input_schema"] = _derive_prompt_node_input_schema(
                node, document_types, instruction_loader
            )
    for node_id, node in prompt_nodes.items():
        with _prompt_node_context(node_id, node):
            if node["kind"] == "extract":
                node["output_schema"] = _derive_extract_output_schema(
                    node, node_id, nodes, edges, document_types, instruction_loader
                )
            else:
                node["output_schema"] = _derive_agent_output_schema()
    _derive_unpack_schemas(nodes, edges)

    return result


@contextlib.contextmanager
def _prompt_node_context(node_id: str, node: dict[str, Any]) -> Iterator[None]:
    """Prefix derivation errors with the node and the template it reads."""

    try:
        yield
    except ValueError as exc:
        source = (
            f"instruction '{node['instruction']}'" if node.get("instruction") else "inline template"
        )
        raise ValueError(f"{node['kind']} node '{node_id}' ({source}): {exc}") from None


def _raw_output_port(node: Any, port: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return (port schema, root definitions) for one output port of a raw node dict."""

    schema = node.get("output_schema") if isinstance(node, dict) else None
    if not isinstance(schema, dict):
        return None, {}
    definitions = {
        key: schema[key] for key in _SCHEMA_DEFINITION_KEYS if isinstance(schema.get(key), dict)
    }
    if node.get("kind") == "data":
        return (schema if port == "value" else None), definitions
    properties = schema.get("properties") if schema.get("type") == "object" else None
    port_schema = properties.get(port) if isinstance(properties, dict) else None
    return (port_schema if isinstance(port_schema, dict) else None), definitions


def _derive_unpack_schemas(nodes: dict[str, Any], edges: Any) -> None:
    unpack_ids = [
        node_id
        for node_id, node in nodes.items()
        if isinstance(node, dict) and node.get("kind") == "unpack"
    ]
    if not unpack_ids:
        return
    feeds: dict[str, tuple[str, str]] = {}
    for edge in edges if isinstance(edges, list) else []:
        source = edge.get("from") if isinstance(edge, dict) else None
        target = edge.get("to") if isinstance(edge, dict) else None
        if (
            isinstance(source, dict)
            and isinstance(target, dict)
            and target.get("node") in unpack_ids
            and target.get("port") == "value"
        ):
            feeds[str(target["node"])] = (str(source.get("node")), str(source.get("port")))
    # Each pass settles at least one more link of any Unpack chain; cycles are
    # rejected later by graph validation, so the pass count is bounded.
    for _ in range(len(unpack_ids) + 1):
        changed = False
        for node_id in unpack_ids:
            node = nodes[node_id]
            source_node_id, source_port = feeds.get(node_id, ("", ""))
            source, definitions = _raw_output_port(nodes.get(source_node_id), source_port)
            input_schema = _unpack_input_schema(source, definitions)
            output_schema = _unpack_output_schema(source, definitions)
            if (
                node.get("input_schema") != input_schema
                or node.get("output_schema") != output_schema
            ):
                node["input_schema"] = input_schema
                node["output_schema"] = output_schema
                changed = True
        if not changed:
            break
