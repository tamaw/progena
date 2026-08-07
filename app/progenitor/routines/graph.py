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

ROUTINE_GRAPH_VERSION = "2.0.0"
ROUTINE_SEED_NODE_ID = "__seed__"
ROUTINE_PROJECT_NODE_ID = "__project_output__"
# START and END are LangGraph's executable sentinel ids, not user nodes. Keeping
# them out of documents prevents a graph author from shadowing compiler wiring.
RESERVED_ROUTINE_NODE_IDS = frozenset(
    {ROUTINE_SEED_NODE_ID, ROUTINE_PROJECT_NODE_ID, "__start__", "__end__"}
)
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


class RetryExhaustedConfig(BaseModel):
    """Recovery destination after LangGraph has exhausted the retry policy."""

    model_config = ConfigDict(extra="forbid")

    route_to: str = Field(min_length=1)


class RetryPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: int | None = Field(default=None, ge=1)
    initial_interval: float | None = Field(default=None, ge=0)
    backoff_factor: float | None = Field(default=None, ge=1)
    max_interval: float | None = Field(default=None, ge=0)
    jitter: bool | None = None
    on_exhausted: RetryExhaustedConfig | None = None


class CachePolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ttl: int | None = Field(default=None, ge=0)


class LoopGuardConfig(BaseModel):
    """A deterministic guard for a normal or fan-out edge."""

    model_config = ConfigDict(extra="forbid")

    expression: str = Field(min_length=1)
    inputs: dict[str, str] = Field(default_factory=dict)
    on_false: str | None = None


class PromptNodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_template: str | None = None
    prompt_file: str | None = None
    system_prompt: str | None = None
    response_schema: dict[str, Any] | None = None
    retry: RetryPolicyConfig | None = None
    cache: bool | CachePolicyConfig | None = None


class CodeNodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Literal["python"] | None = None
    expression: str | None = None
    expression_file: str | None = None
    inputs: dict[str, str] = Field(default_factory=dict)
    retry: RetryPolicyConfig | None = None
    cache: bool | CachePolicyConfig | None = None


class EvalNodeConfig(CodeNodeConfig):
    rubric_prompt: str | None = None
    response_schema: dict[str, Any] | None = None


class SubagentNodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subagent_name: str = Field(min_length=1)
    prompt_template: str | None = None
    prompt_file: str | None = None
    retry: RetryPolicyConfig | None = None
    cache: bool | CachePolicyConfig | None = None


class AgentNodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: str = Field(min_length=1)
    prompt_template: str | None = None
    prompt_file: str | None = None
    response_schema: dict[str, Any] | None = None
    retry: RetryPolicyConfig | None = None
    cache: bool | CachePolicyConfig | None = None


class NoopNodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HumanNodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    allowed_responses: list[str] | None = None
    retry: RetryPolicyConfig | None = None


class ForeachSendConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["send"]
    items_from: str = Field(min_length=1)
    item_key: str = Field(default="item", min_length=1)
    max_items: int | None = Field(default=None, ge=1)


class ForeachAgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["agent"]
    items_from: str = Field(min_length=1)
    agent_name: str = Field(min_length=1)
    prompt_template: str = Field(min_length=1)
    item_key: str = Field(default="item", min_length=1)
    max_items: int | None = Field(default=None, ge=1)
    max_concurrency: int | None = Field(default=None, ge=1)
    retry: RetryPolicyConfig | None = None
    cache: bool | CachePolicyConfig | None = None


ForeachNodeConfig = Annotated[
    ForeachSendConfig | ForeachAgentConfig,
    Field(discriminator="mode"),
]


class SubgraphNodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subgraph_name: str = Field(min_length=1)
    input_map: dict[str, str] = Field(default_factory=dict)
    output_map: dict[str, str] = Field(default_factory=dict)
    retry: RetryPolicyConfig | None = None
    cache: bool | CachePolicyConfig | None = None


class RoutineNodePrompt(BaseModel):
    """Render a template and invoke the supplied chat model."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["prompt"] = "prompt"
    description: str = ""
    output_key: str | None = None
    config: PromptNodeConfig
    label: str | None = None
    x: float | None = None
    y: float | None = None


class RoutineNodeCode(BaseModel):
    """Evaluate a restricted deterministic expression."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["code"] = "code"
    description: str = ""
    output_key: str | None = None
    config: CodeNodeConfig
    label: str | None = None
    x: float | None = None
    y: float | None = None


class RoutineNodeEval(BaseModel):
    """Evaluate a restricted expression, or ask the model for a rubric result."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["eval"] = "eval"
    description: str = ""
    output_key: str | None = None
    config: EvalNodeConfig
    label: str | None = None
    x: float | None = None
    y: float | None = None


class RoutineNodeSubagent(BaseModel):
    """Invoke another compiled routine graph by name."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["subagent"] = "subagent"
    description: str = ""
    output_key: str | None = None
    config: SubagentNodeConfig
    label: str | None = None
    x: float | None = None
    y: float | None = None


class RoutineNodeAgent(BaseModel):
    """Invoke a declarative object agent by name."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["agent"] = "agent"
    description: str = ""
    output_key: str | None = None
    config: AgentNodeConfig
    label: str | None = None
    x: float | None = None
    y: float | None = None


class RoutineNodeHuman(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["human"] = "human"
    description: str = ""
    output_key: str | None = None
    config: HumanNodeConfig
    label: str | None = None
    x: float | None = None
    y: float | None = None


class RoutineNodeForeach(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["foreach"] = "foreach"
    description: str = ""
    output_key: str | None = None
    config: ForeachNodeConfig
    label: str | None = None
    x: float | None = None
    y: float | None = None


class RoutineNodeSubgraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["subgraph"] = "subgraph"
    description: str = ""
    output_key: str | None = None
    config: SubgraphNodeConfig
    label: str | None = None
    x: float | None = None
    y: float | None = None


class RoutineNodeNoop(BaseModel):
    """Pass through without changing state."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["noop"] = "noop"
    description: str = ""
    output_key: str | None = None
    config: NoopNodeConfig = Field(default_factory=NoopNodeConfig)
    label: str | None = None
    x: float | None = None
    y: float | None = None


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


class RoutineSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retry: RetryPolicyConfig | None = None
    recursion_limit: int | None = Field(default=None, ge=1)
    cache: bool | None = None


class RoutineEdge(BaseModel):
    """A normal, static fan-out, barrier fan-in, or conditional router edge."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str | list[str] = Field(alias="from")
    to: str | list[str] | None = None
    level: Literal["normal", "fan_out", "fan_in", "route"]
    router: Literal["state_equals"] | None = None
    state_key: str | None = None
    branches: dict[str, str] | None = None
    default: str | None = None
    loop_guard: LoopGuardConfig | None = None

    @property
    def is_router(self) -> bool:
        return self.level == "route"

    @property
    def source_ids(self) -> list[str]:
        return [self.from_] if isinstance(self.from_, str) else self.from_

    @property
    def target_ids(self) -> list[str]:
        if self.to is None:
            return []
        return [self.to] if isinstance(self.to, str) else self.to


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
    settings: RoutineSettings = Field(default_factory=RoutineSettings)
    input_schema: dict[str, str] | None = None
    output_schema: dict[str, str] | None = None
    defs: dict[str, dict[str, Any]] | None = Field(default=None, alias="$defs")


def _node_output_key(node: RoutineNode) -> str:
    return node.output_key or node.id


def _as_node_ids(value: Any, *, field: str) -> list[str]:
    if isinstance(value, str) and value:
        return [value]
    if isinstance(value, list) and value and all(isinstance(item, str) and item for item in value):
        return value
    raise ValueError(f"edge {field} must be a non-empty node id or node-id list")


def _retry_recovery_target(config: Any) -> str | None:
    if not isinstance(config, dict):
        return None
    retry = config.get("retry")
    if not isinstance(retry, dict):
        return None
    exhausted = retry.get("on_exhausted")
    return exhausted.get("route_to") if isinstance(exhausted, dict) else None


def validate_routine_graph(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    """Validate graph topology and cross-reference constraints.

    JSON Schema owns field types and the four edge shapes. This layer owns
    cross-edge invariants: one outgoing edge declaration per node, all named
    destinations exist, Send fan-out has a visible normal edge, and recovery/
    guard routes point at graph nodes.
    """
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("routine graph nodes and edges must be lists")

    ids: set[str] = set()
    nodes_by_id: dict[str, dict[str, Any]] = {}
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
        nodes_by_id[node_id] = node
        kind = node.get("kind")
        if kind not in VALID_ROUTINE_NODE_KINDS:
            raise ValueError(f"unknown node kind: {kind}")

    outgoing: dict[str, dict[str, Any]] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("each edge must be an object")
        level = edge.get("level")
        sources = _as_node_ids(edge.get("from"), field="from")
        if len(set(sources)) != len(sources):
            raise ValueError("edge from list cannot repeat a node id")
        for source in sources:
            if source not in ids:
                raise ValueError(f"edge references unknown source node: {source}")
            if source in outgoing:
                raise ValueError(f"node has more than one outgoing edge declaration: {source}")
            outgoing[source] = edge

        if level == "route":
            if len(sources) != 1 or not isinstance(edge.get("from"), str):
                raise ValueError("router edge from must be one node id")
            if edge.get("router") != "state_equals":
                raise ValueError(f"unsupported router kind: {edge.get('router')}")
            branches = edge.get("branches") or {}
            if not isinstance(branches, dict) or not branches:
                raise ValueError(f"router edge from {sources[0]} needs at least one branch")
            targets = list(branches.values())
            default = edge.get("default")
            if default is not None:
                targets.append(default)
        else:
            targets = _as_node_ids(edge.get("to"), field="to")
            if level == "normal" and (len(sources) != 1 or len(targets) != 1):
                raise ValueError("normal edge requires one source and one target")
            if level == "fan_out" and (len(sources) != 1 or len(targets) < 2):
                raise ValueError("fan_out edge requires one source and at least two targets")
            if level == "fan_in" and (len(sources) < 2 or len(targets) != 1):
                raise ValueError("fan_in edge requires at least two sources and one target")
            if level not in {"normal", "fan_out", "fan_in"}:
                raise ValueError(f"unsupported edge level: {level}")
            if level == "fan_in" and edge.get("loop_guard") is not None:
                raise ValueError("fan_in edge cannot have a loop_guard")

        for target in targets:
            if target not in ids:
                raise ValueError(f"edge references unknown target node: {target}")

        guard = edge.get("loop_guard")
        if isinstance(guard, dict) and guard.get("on_false") is not None:
            target = guard["on_false"]
            if target not in ids:
                raise ValueError(f"loop_guard routes to unknown node: {target}")

    for node_id, node in nodes_by_id.items():
        config = node.get("config")
        recovery = _retry_recovery_target(config)
        if recovery is not None and recovery not in ids:
            raise ValueError(f"node {node_id} retry recovery routes to unknown node: {recovery}")
        if (
            node.get("kind") == "foreach"
            and isinstance(config, dict)
            and config.get("mode") == "send"
        ):
            edge = outgoing.get(node_id)
            if edge is None or edge.get("level") != "normal":
                raise ValueError(f"foreach Send node {node_id} needs one normal outgoing edge")


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
    settings_retry = _retry_recovery_target(payload.get("settings"))
    if settings_retry is not None and settings_retry not in node_ids:
        raise ValueError(f"graph retry recovery routes to unknown node: {settings_retry}")

    return RoutineGraph.model_validate(payload)
