"""Shared routine-graph definitions and validation.

Mirrors :mod:`app.progenitor.workflows`'s split: this module owns the schema and
Pydantic contract; :mod:`app.backend.agent_profile_routes` imports from here rather
than defining its own node-kind vocabulary.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from ..schemas import load_schema
from ..structured import validate_against_schema

ROUTINE_GRAPH_VERSION = "1.0.0"
ROUTINE_SEED_NODE_ID = "__seed__"
ROUTINE_PROJECT_NODE_ID = "__project_output__"
RESERVED_ROUTINE_NODE_IDS = frozenset({ROUTINE_SEED_NODE_ID, ROUTINE_PROJECT_NODE_ID})
VALID_ROUTINE_NODE_KINDS = {
    "prompt",
    "code",
    "eval",
    "subagent",
    "agent",
    "noop",
    "human",
    "foreach",
    "subgraph",
}

ROUTINE_NODE_RETRY_DEFAULTS = {
    "initial_interval": 0.1,
    "backoff_factor": 2.0,
    "max_interval": 5.0,
    "max_attempts": 3,
    "jitter": False,
}


class RoutineContext(TypedDict, total=False):
    """Per-invocation values injected into every routine node."""

    user_id: str
    workspace_id: str
    request_id: str
    metadata: dict[str, Any]


class RoutineNodePrompt(BaseModel):
    """Render a template and invoke the supplied chat model."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["prompt"] = "prompt"
    description: str = ""
    output_key: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None


class RoutineNodeCode(BaseModel):
    """Evaluate a restricted deterministic expression."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["code"] = "code"
    description: str = ""
    output_key: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None


class RoutineNodeEval(BaseModel):
    """Evaluate a restricted expression, or ask the model for a rubric result."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["eval"] = "eval"
    description: str = ""
    output_key: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None


class RoutineNodeSubagent(BaseModel):
    """Invoke another compiled routine graph by name."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["subagent"] = "subagent"
    description: str = ""
    output_key: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None


class RoutineNodeAgent(BaseModel):
    """Invoke a declarative object agent by name."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["agent"] = "agent"
    description: str = ""
    output_key: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None


class RoutineNodeHuman(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["human"] = "human"
    description: str = ""
    output_key: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None


class RoutineNodeForeach(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["foreach"] = "foreach"
    description: str = ""
    output_key: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None


class RoutineNodeSubgraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["subgraph"] = "subgraph"
    description: str = ""
    output_key: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None


class RoutineNodeNoop(BaseModel):
    """Pass through without changing state."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["noop"] = "noop"
    description: str = ""
    output_key: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None


RoutineNode = Annotated[
    RoutineNodePrompt
    | RoutineNodeCode
    | RoutineNodeEval
    | RoutineNodeSubagent
    | RoutineNodeAgent
    | RoutineNodeNoop
    | RoutineNodeHuman
    | RoutineNodeForeach
    | RoutineNodeSubgraph,
    Field(discriminator="kind"),
]


class RoutineEdge(BaseModel):
    """A plain ``from -> to`` edge, or a ``state_equals`` router edge.

    Router edges omit ``to`` and instead carry ``router``/``state_key``/``branches``/
    ``default``. Modeled as one class with optional fields (rather than a discriminated
    union) since the JSON Schema check (``graph.routine.schema.v1.json``, validated before
    this model in :func:`validate_routine_graph_payload`) already enforces the two shapes;
    this mirrors how prior JSON-schema-then-Pydantic validation is layered elsewhere in this
    module's sibling, :mod:`app.progenitor.workflows`.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from")
    to: str | None = None
    router: Literal["state_equals"] | None = None
    state_key: str | None = None
    branches: dict[str, str] | None = None
    default: str | None = None

    @property
    def is_router(self) -> bool:
        return self.router is not None


class RoutineGraph(BaseModel):
    """A validated, executable routine graph."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    version: str = ROUTINE_GRAPH_VERSION
    entry: str
    nodes: list[RoutineNode]
    edges: list[RoutineEdge] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, str] | None = None
    output_schema: dict[str, str] | None = None
    defs: dict[str, dict[str, Any]] | None = Field(default=None, alias="$defs")


def _node_output_key(node: RoutineNode) -> str:
    return node.output_key or node.id


def validate_routine_graph(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    """Validate a routine graph's structural shape. Raises ``ValueError`` on bad input."""
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("routine graph nodes and edges must be lists")

    ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("each node must be an object")
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("each node needs a non-empty string id")
        if node_id in RESERVED_ROUTINE_NODE_IDS:
            raise ValueError(f"node id is reserved: {node_id}")
        if node_id in ids:
            raise ValueError(f"duplicate node id: {node_id}")
        ids.add(node_id)
        kind = node.get("kind")
        if kind not in VALID_ROUTINE_NODE_KINDS:
            raise ValueError(f"unknown node kind: {kind}")

    router_sources: set[str] = set()
    plain_sources: set[str] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("each edge must be an object")
        source = edge.get("from")
        if source not in ids:
            raise ValueError(f"edge references unknown source node: {source}")
        if edge.get("router") is not None:
            if edge.get("router") != "state_equals":
                raise ValueError(f"unsupported router kind: {edge.get('router')}")
            if source in router_sources:
                raise ValueError(f"node has more than one router edge: {source}")
            router_sources.add(source)
            branches = edge.get("branches") or {}
            if not isinstance(branches, dict) or not branches:
                raise ValueError(f"router edge from {source} needs at least one branch")
            for target in branches.values():
                if target not in ids:
                    raise ValueError(
                        f"router edge from {source} branches to unknown node: {target}"
                    )
            default = edge.get("default")
            if default is not None and default not in ids:
                raise ValueError(f"router edge from {source} defaults to unknown node: {default}")
        else:
            target = edge.get("to")
            if target not in ids:
                raise ValueError(f"edge references unknown target node: {target}")
            if source in plain_sources:
                raise ValueError(f"node has more than one plain edge: {source}")
            plain_sources.add(source)
            if source in router_sources:
                raise ValueError(f"node mixes a router edge with a plain edge: {source}")

    for source in router_sources:
        plain_from_same_source = any(
            e.get("from") == source and e.get("router") is None for e in edges
        )
        if plain_from_same_source:
            raise ValueError(f"node mixes a router edge with a plain edge: {source}")


def validate_routine_graph_payload(payload: dict[str, Any]) -> RoutineGraph:
    """Validate a full routine graph document against the JSON schema, then parse it."""
    schema = load_schema("graph.routine")
    schema_error = validate_against_schema(payload, schema)
    if schema_error:
        raise ValueError(f"schema validation failed: {schema_error}")

    nodes = payload.get("nodes", [])
    edges = payload.get("edges", [])
    validate_routine_graph(nodes, edges)

    entry = payload.get("entry")
    node_ids = {node["id"] for node in nodes}
    if entry not in node_ids:
        raise ValueError(f"entry references unknown node: {entry}")

    return RoutineGraph.model_validate(payload)
