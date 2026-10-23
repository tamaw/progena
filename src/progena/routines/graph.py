"""Shared routine-graph definitions and validation.

Mirrors :mod:`src.progena.workflows`'s split: this module owns the schema and
Pydantic contract; :mod:`src.backend.agents.routes` imports from here rather
than defining its own node-kind vocabulary.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from src.progena.documents.catalog import load_schema
from src.progena.llm.structured import validate_against_schema

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
    "eval",
    "condition",
    "python",
    "switch",
    "split",
    "classify",
    "agent",
    "a2a",
    "noop",
    "human",
    "foreach",
    "routine",
    "reduce",
}

# A `condition` node is a two-way gate: its expression is coerced to a bool, so
# its router's branch set is fixed rather than author-defined.
CONDITION_BRANCH_VALUES = frozenset({"true", "false"})

# Counted as retries, not attempts: 0 means the node runs once and is never
# retried, which is also what omitting the whole `retry` block does.
ROUTINE_NODE_RETRY_DEFAULTS = {
    "initial_interval": 0.1,
    "backoff_factor": 2.0,
    "max_interval": 5.0,
    "max_retries": 2,
    # A foreach fan-out fails its branches together, so unjittered backoff
    # marches them all back into the same failing endpoint in lockstep.
    "jitter": True,
}

# The usual way an agent step fails is by hanging, not by raising, so every
# node is bounded unless its author says otherwise. Matches the shell tool's
# PROGENA_SHELL_TIMEOUT_SECONDS default.
ROUTINE_NODE_TIMEOUT_SECONDS = 120.0


class RoutineContext(TypedDict, total=False):
    """Per-invocation values injected into every routine node."""

    user_id: str
    workspace_id: str
    request_id: str
    metadata: dict[str, Any]
    execution_policy: dict[str, Any]
    _routine_pending_children: dict[str, Any]


class RetryPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_retries: int | None = Field(default=None, ge=0)
    initial_interval: float | None = Field(default=None, ge=0)
    backoff_factor: float | None = Field(default=None, ge=1)
    max_interval: float | None = Field(default=None, ge=0)
    jitter: bool | None = None
    retry_on: Literal["transient", "model_response", "all"] | None = None


class CachePolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ttl: int | None = Field(default=None, ge=0)


class LoopGuardConfig(BaseModel):
    """A deterministic guard for a normal edge. `expression` resolves names
    directly against the routine's own `data` state -- see EvalNodeConfig's
    `defaults` comment for the same top-level-only fallback semantics."""

    model_config = ConfigDict(extra="forbid")

    expression: str = Field(min_length=1)
    on_false: str | None = None


class PromptNodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_profile: str | None = Field(default=None, min_length=1)

    prompt_template: str | None = None
    prompt_file: str | None = None
    system_prompt: str | None = None
    response_schema: dict[str, Any] | None = None
    retry: RetryPolicyConfig | None = None
    timeout: float | None = Field(default=None, ge=0)
    cache: bool | CachePolicyConfig | None = None


class EvalNodeConfig(BaseModel):
    """Deterministic expression evaluation; returns the raw result.

    `expression` resolves names directly against the routine's own `data`
    state (`a.b` walks a nested dict the same way a config-authored dotted
    path does elsewhere -- see expression.py's `ast.Attribute` handling) --
    there is no separate `inputs` binding step; whatever key an upstream
    node wrote to is just referenced by that name."""

    model_config = ConfigDict(extra="forbid")

    language: Literal["python"] | None = None
    expression: str | None = None
    expression_file: str | None = None
    # Per-top-level-name fallback used when `expression` references a state
    # key that doesn't exist yet -- the strict KeyError-on-missing behavior
    # exists to catch author typos, but a node whose expression legitimately
    # needs to work seedless on its first visit (e.g. an iteration counter
    # reading its own not-yet-written prior output) needs an escape hatch.
    # Scoped to `eval` only, and to the top-level name (not a nested `.`
    # segment): `condition`/`switch`/`loop_guard` never hit this case, since
    # anything they'd reference is always written earlier in the same visit.
    defaults: dict[str, Any] = Field(default_factory=dict)
    retry: RetryPolicyConfig | None = None
    timeout: float | None = Field(default=None, ge=0)
    cache: bool | CachePolicyConfig | None = None


class PythonNodeConfig(BaseModel):
    """Python source executed only by the injected remote sandbox runner."""

    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, min_length=1)
    code_file: str | None = Field(default=None, min_length=1)
    input_map: dict[str, str] = Field(default_factory=dict)
    input_files: dict[str, str] = Field(default_factory=dict)
    output_files: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int | None = Field(default=None, ge=1, le=300)
    response_schema: dict[str, Any] | None = None
    retry: RetryPolicyConfig | None = None
    timeout: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_one_code_source(self) -> PythonNodeConfig:
        if (self.code is None) == (self.code_file is None):
            raise ValueError("python node config needs exactly one of 'code' or 'code_file'")
        return self


class ConditionNodeConfig(BaseModel):
    """Deterministic expression evaluation; result is coerced to a bool gate."""

    model_config = ConfigDict(extra="forbid")

    language: Literal["python"] | None = None
    expression: str | None = None
    expression_file: str | None = None
    retry: RetryPolicyConfig | None = None
    timeout: float | None = Field(default=None, ge=0)
    cache: bool | CachePolicyConfig | None = None


class SwitchNodeConfig(BaseModel):
    """Deterministic expression evaluation; result routes on an author-defined,
    open-ended branch set (unlike `condition`, which is fixed to true/false)."""

    model_config = ConfigDict(extra="forbid")

    language: Literal["python"] | None = None
    expression: str | None = None
    expression_file: str | None = None
    retry: RetryPolicyConfig | None = None
    timeout: float | None = Field(default=None, ge=0)
    cache: bool | CachePolicyConfig | None = None


class SplitNodeConfig(BaseModel):
    """No computation: `split` is a structural parallel gateway, not an
    expression node. Its router edge takes every branch unconditionally."""

    model_config = ConfigDict(extra="forbid")


class ClassifyNodeConfig(BaseModel):
    """LLM-judged rubric evaluation against a required structured schema."""

    model_config = ConfigDict(extra="forbid")

    model_profile: str | None = Field(default=None, min_length=1)

    rubric_prompt: str = Field(min_length=1)
    response_schema: dict[str, Any] = Field(...)
    inputs: dict[str, str] = Field(default_factory=dict)
    retry: RetryPolicyConfig | None = None
    timeout: float | None = Field(default=None, ge=0)
    cache: bool | CachePolicyConfig | None = None


class AgentNodeConfig(BaseModel):
    """Conversational delegation to a local object agent."""

    model_config = ConfigDict(extra="forbid")

    model_profile: str | None = Field(default=None, min_length=1)

    agent_name: str = Field(min_length=1)
    execution: Literal["wait", "background"] = "wait"
    prompt_template: str | None = None
    instruction: str | None = None
    inputs: dict[str, str] = Field(default_factory=dict)
    response_schema: dict[str, Any] | None = None
    continue_thread: bool = True
    retry: RetryPolicyConfig | None = None
    timeout: float | None = Field(default=None, ge=0)
    cache: bool | CachePolicyConfig | None = None

    @model_serializer(mode="wrap")
    def serialize_input_source(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        # The schema selects input sources by field presence. An internal
        # empty-map default must not create a second source in a saved graph.
        if (self.prompt_template is not None or self.instruction is not None) and not self.inputs:
            data.pop("inputs", None)
        return data


class A2ANodeConfig(BaseModel):
    """One-shot delegation to a registered Agent2Agent-protocol peer."""

    model_config = ConfigDict(extra="forbid")

    agent_name: str = Field(min_length=1)
    prompt_template: str | None = None
    prompt_file: str | None = None
    retry: RetryPolicyConfig | None = None
    timeout: float | None = Field(default=None, ge=0)
    cache: bool | CachePolicyConfig | None = None


class NoopNodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HumanNodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    allowed_responses: list[str] | None = None
    # "human": only a person may answer (the UI); "any": Progena may too.
    approver: Literal["human", "any"] = "any"
    retry: RetryPolicyConfig | None = None
    timeout: float | None = Field(default=None, ge=0)


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
    timeout: float | None = Field(default=None, ge=0)
    cache: bool | CachePolicyConfig | None = None


ForeachNodeConfig = Annotated[
    ForeachSendConfig | ForeachAgentConfig,
    Field(discriminator="mode"),
]


class RoutineNodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routine_name: str = Field(min_length=1)
    input_map: dict[str, str] = Field(default_factory=dict)
    output_map: dict[str, str] = Field(default_factory=dict)
    retry: RetryPolicyConfig | None = None
    timeout: float | None = Field(default=None, ge=0)
    cache: bool | CachePolicyConfig | None = None


class ReduceNodeConfig(BaseModel):
    """Aggregate values emitted by repeated executions of one map/body node."""

    model_config = ConfigDict(extra="forbid")

    source_node: str = Field(min_length=1)
    operation: Literal["collect", "sum", "count", "min", "max", "product"] = "collect"


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


class RoutineNodeEval(BaseModel):
    """Evaluate a restricted deterministic expression; returns the raw result."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["eval"] = "eval"
    description: str = ""
    output_key: str | None = None
    config: EvalNodeConfig
    label: str | None = None
    x: float | None = None
    y: float | None = None


class RoutineNodePython(BaseModel):
    """Execute author-provided Python remotely through the sandbox boundary."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["python"] = "python"
    description: str = ""
    output_key: str | None = None
    config: PythonNodeConfig
    label: str | None = None
    x: float | None = None
    y: float | None = None


class RoutineNodeCondition(BaseModel):
    """Evaluate a restricted expression, coerced to a boolean gate."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["condition"] = "condition"
    description: str = ""
    output_key: str | None = None
    config: ConditionNodeConfig
    label: str | None = None
    x: float | None = None
    y: float | None = None


class RoutineNodeSwitch(BaseModel):
    """Evaluate a restricted expression; routes on an author-defined,
    open-ended branch set instead of a fixed boolean gate."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["switch"] = "switch"
    description: str = ""
    output_key: str | None = None
    config: SwitchNodeConfig
    label: str | None = None
    x: float | None = None
    y: float | None = None


class RoutineNodeSplit(BaseModel):
    """Structural parallel gateway: every branch on its router edge runs,
    unconditionally -- no expression, no picking."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["split"] = "split"
    description: str = ""
    output_key: str | None = None
    config: SplitNodeConfig = Field(default_factory=SplitNodeConfig)
    label: str | None = None
    x: float | None = None
    y: float | None = None


class RoutineNodeClassify(BaseModel):
    """Ask the model to judge a rubric against a required structured schema."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["classify"] = "classify"
    description: str = ""
    output_key: str | None = None
    config: ClassifyNodeConfig
    label: str | None = None
    x: float | None = None
    y: float | None = None


class RoutineNodeAgent(BaseModel):
    """Conversationally invoke a local object agent by name."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["agent"] = "agent"
    description: str = ""
    output_key: str | None = None
    config: AgentNodeConfig
    label: str | None = None
    x: float | None = None
    y: float | None = None


class RoutineNodeA2A(BaseModel):
    """Invoke a registered Agent2Agent-protocol peer by name."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["a2a"] = "a2a"
    description: str = ""
    output_key: str | None = None
    config: A2ANodeConfig
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


class RoutineNodeRoutine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["routine"] = "routine"
    description: str = ""
    output_key: str | None = None
    config: RoutineNodeConfig
    label: str | None = None
    x: float | None = None
    y: float | None = None


class RoutineNodeReduce(BaseModel):
    """Reduce outputs captured from repeated executions of a source node."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["reduce"] = "reduce"
    description: str = ""
    output_key: str | None = None
    config: ReduceNodeConfig
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
    | RoutineNodeEval
    | RoutineNodeCondition
    | RoutineNodeSwitch
    | RoutineNodeSplit
    | RoutineNodeClassify
    | RoutineNodeAgent
    | RoutineNodeA2A
    | RoutineNodeNoop
    | RoutineNodePython
    | RoutineNodeHuman
    | RoutineNodeForeach
    | RoutineNodeRoutine
    | RoutineNodeReduce,
    Field(discriminator="kind"),
]


class RoutineEdge(BaseModel):
    """A normal edge, barrier fan-in, router, foreach empty route, or error edge."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str | list[str] = Field(alias="from")
    to: str | list[str] | None = None
    level: Literal["normal", "fan_in", "route", "foreach_empty", "error"]
    router: Literal["state_equals"] | None = None
    state_key: str | None = None
    branches: dict[str, str] | None = None
    default: str | None = None
    loop_guard: LoopGuardConfig | None = None

    @property
    def is_router(self) -> bool:
        return self.level == "route"

    @property
    def is_flow(self) -> bool:
        """Whether this edge carries ordinary execution.

        Error edges name recovery paths, while foreach-empty edges are fallback
        routes compiled alongside the Send router. Neither is an ordinary
        successor edge or part of the one-outgoing-edge rule.
        """
        return self.level not in {"error", "foreach_empty"}

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
    policy_id: str = Field(default="default", min_length=1)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    defs: dict[str, dict[str, Any]] | None = Field(default=None, alias="$defs")


def _node_output_key(node: RoutineNode) -> str:
    return node.output_key or node.id


def _as_node_ids(value: Any, *, field: str) -> list[str]:
    if isinstance(value, str) and value:
        return [value]
    if isinstance(value, list) and value and all(isinstance(item, str) and item for item in value):
        return value
    raise ValueError(f"edge {field} must be a non-empty node id or node-id list")


def error_targets(edges: list[dict[str, Any]]) -> dict[str, str]:
    """Map each node id to where its failures go, for the error edges present."""
    return {
        edge["from"]: edge["to"]
        for edge in edges
        if isinstance(edge, dict)
        and edge.get("level") == "error"
        and isinstance(edge.get("from"), str)
        and isinstance(edge.get("to"), str)
    }


def validate_routine_graph(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    """Validate graph topology and cross-reference constraints.

    JSON Schema owns field types and the four edge shapes. This layer owns
    cross-edge invariants: one outgoing edge declaration per node, at most one
    error edge per node, all named destinations exist, Send fan-out has a
    visible normal edge, and guard routes point at graph nodes.
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
    failing: set[str] = set()
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
        # These edges are additional routing metadata, not ordinary graph
        # successors: an error edge names recovery; foreach_empty is evaluated
        # by the Send router only when it has no work items.
        if level in {"error", "foreach_empty"}:
            if len(sources) != 1 or not isinstance(edge.get("from"), str):
                raise ValueError(f"{level} edge from must be one node id")
            if level == "error":
                if sources[0] in failing:
                    raise ValueError(f"node has more than one error edge: {sources[0]}")
                failing.add(sources[0])
        else:
            for source in sources:
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
            if level == "fan_in" and (len(sources) < 2 or len(targets) != 1):
                raise ValueError("fan_in edge requires at least two sources and one target")
            if level == "error":
                if len(targets) != 1:
                    raise ValueError("error edge requires one source and one target")
                if targets[0] == sources[0]:
                    raise ValueError(f"node cannot route its own errors to itself: {sources[0]}")
            if level not in {"normal", "fan_in", "error", "foreach_empty"}:
                raise ValueError(f"unsupported edge level: {level}")
            if level != "normal" and edge.get("loop_guard") is not None:
                raise ValueError(f"{level} edge cannot have a loop_guard")

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
        if node.get("kind") == "reduce":
            source_id = (config or {}).get("source_node") if isinstance(config, dict) else None
            if source_id not in nodes_by_id:
                raise ValueError(
                    f"reduce node {node_id} references unknown source node: {source_id}"
                )
            if source_id == node_id:
                raise ValueError(f"reduce node {node_id} cannot reduce its own output")
        empty_edges = [
            edge
            for edge in edges
            if edge.get("level") == "foreach_empty" and edge.get("from") == node_id
        ]
        if empty_edges and not (
            node.get("kind") == "foreach"
            and isinstance(config, dict)
            and config.get("mode") == "send"
        ):
            raise ValueError(f"foreach_empty edge source must be a Send foreach node: {node_id}")
        if len(empty_edges) > 1:
            raise ValueError(f"foreach Send node {node_id} has more than one empty route")
        if (
            node.get("kind") == "foreach"
            and isinstance(config, dict)
            and config.get("mode") == "send"
        ):
            edge = outgoing.get(node_id)
            if edge is None or edge.get("level") != "normal":
                raise ValueError(f"foreach Send node {node_id} needs one normal outgoing edge")
        # A `condition` node coerces its expression to a bool, so the only two
        # values it can ever route on are JSON's `true`/`false` (see
        # factory._branch_key). Both must be wired -- a default would be
        # unreachable, and a missing arm would be a runtime dead end.
        if node.get("kind") == "condition":
            edge = outgoing.get(node_id)
            if edge is not None and edge.get("level") == "route":
                if set(edge.get("branches") or {}) != CONDITION_BRANCH_VALUES:
                    raise ValueError(
                        f"condition node {node_id} needs exactly 'true' and 'false' branches"
                    )
                if edge.get("default") is not None:
                    raise ValueError(f"condition node {node_id} cannot have a default branch")


def validate_routine_graph_payload(payload: dict[str, Any]) -> RoutineGraph:
    """Validate a full routine graph document against the JSON schema, then parse it."""
    schema = load_schema("graph.routine")
    schema_error = validate_against_schema(payload, schema)
    if schema_error:
        raise ValueError(f"schema validation failed: {schema_error}")

    for schema_name in ("input_schema", "output_schema"):
        if "_execution_policy" in ((payload.get(schema_name) or {}).get("properties") or {}):
            raise ValueError("_execution_policy is reserved execution metadata")
    if any(node.get("output_key") == "_execution_policy" for node in payload.get("nodes", [])):
        raise ValueError("_execution_policy is reserved execution metadata")
    nodes = payload.get("nodes", [])
    edges = payload.get("edges", [])
    validate_routine_graph(nodes, edges)
    entry = payload.get("entry")
    node_ids = {node["id"] for node in nodes}
    if entry not in node_ids:
        raise ValueError(f"entry references unknown node: {entry}")

    return RoutineGraph.model_validate(payload)
