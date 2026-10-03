import { routerTargets, edgeSourceIds, isFlowEdge, reachableFrom } from './graphTopology';


// What each kind must carry before the backend will accept it, mirroring the
// `required`/`oneOf` blocks of the *Config defs in graph.routine.schema.v1.json.
// A nested array means "exactly one of these" (a prompt takes an inline
// template or a file, never both). Kept as data rather than spread through the
// field renderers so the inspector, the canvas badge and the autosave gate all
// read one source -- and so it stays diffable against the schema it mirrors.
export const REQUIRED_CONFIG = {
  prompt: [['prompt_template', 'prompt_file']],
  eval: [['expression', 'expression_file']],
  condition: [['expression', 'expression_file']],
  switch: [['expression', 'expression_file']],
  split: [],
  classify: ['rubric_prompt', 'response_schema'],
  agent: ['agent_name', ['prompt_template', 'prompt_file', 'inputs']],
  a2a: ['agent_name', ['prompt_template', 'prompt_file', 'inputs']],
  human: ['message'],
  routine: ['routine_name'],
  noop: [],
};

function isBlank(value) {
  if (value === undefined || value === null) return true;
  if (typeof value === 'string') return value.trim() === '';
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === 'object') return Object.keys(value).length === 0;
  return false;
}

// `foreach` is the one kind whose requirements depend on its own config: the
// schema splits it into foreachSendConfig/foreachAgentConfig on `mode`.
function requiredFieldsFor(node) {
  if (node?.kind === 'foreach') {
    return node.config?.mode === 'agent'
      ? ['items_from', 'agent_name', 'prompt_template']
      : ['items_from'];
  }
  return REQUIRED_CONFIG[node?.kind] || [];
}

// The requirements a node has not met, one entry per requirement. A one-of
// group stays grouped -- "a prompt needs a template or a file" is a single
// unmet requirement offering two ways to satisfy it, so counting its members
// would tell the author two fields are missing when either one will do.
export function missingRequirements(node) {
  const config = node?.config || {};
  return requiredFieldsFor(node)
    .map((requirement) => (Array.isArray(requirement) ? requirement : [requirement]))
    .filter((group) => group.every((field) => isBlank(config[field])));
}

// The same thing flattened to field names, for marking every alternative in
// the inspector and for deciding whether the graph is safe to PATCH.
export function missingRequiredConfig(node) {
  return missingRequirements(node).flat();
}

// One predicate behind the inspector lock, the canvas badge and the autosave
// gate, so a step can never look complete in one place and not another.
export function isNodeComplete(node) {
  return missingRequirements(node).length === 0;
}

// Nodes the backend would reject, in canvas order. Empty means the graph is
// safe to PATCH as far as per-node completeness goes.
export function incompleteNodes(nodes) {
  return (nodes || [])
    .map((node) => ({ node, missing: missingRequiredConfig(node) }))
    .filter((entry) => entry.missing.length > 0);
}

// Backend's LoopGuardConfig requires a non-empty expression -- a fresh
// Loop's tail starts blank (see insertAt), same "not saved yet" gate as an
// Eval/Condition with an empty expression, just read off the edge instead
// of a node's config.
export function incompleteLoopEdges(edges) {
  return (edges || []).filter((edge) => edge.loop_guard && !edge.loop_guard.expression?.trim());
}
// Exported for unit coverage: regression guard for the fan_in-edge bug
// (edge.from is an array for fan_in, but this validator used to compare/look
// it up as a plain string, so every graph with a router-fork "Join branches"
// join permanently failed validation and could never enable Run).
export function validateWorkingGraph(nodes, edges, entry, runtimeProfiles = null) {
  const errors = [];
  const ids = new Set(nodes.map((node) => node.id).filter(Boolean));
  if (!entry || !ids.has(entry)) errors.push('Entry must reference an existing node.');
  nodes.forEach((node) => {
    if (!node.id) errors.push('Every node needs an id.');
    if (node.id === '__seed__') errors.push('__seed__ is reserved.');
  });
  edges.forEach((edge) => {
    const targets = edge.router ? routerTargets(edge) : [edge.to];
    edgeSourceIds(edge).forEach((source) => { if (!ids.has(source)) errors.push(`Edge source "${source}" is missing.`); });
    targets.forEach((target) => {
      if (!ids.has(target)) errors.push(`Edge target "${target}" is missing.`);
    });
  });
  // Mirrors the backend's one-outgoing-edge-declaration rule. A step that
  // feeds a join is named as a source *by* the join, so anything that also
  // hands it a plain edge (splicing a step in after it, say) leaves it with
  // two ways out. Checking it here surfaces the break in the editor instead
  // of only as a rejected save. Error edges do NOT count -- a node may have
  // both one flow edge and one error edge.
  const outgoingCount = new Map();
  edges.filter(isFlowEdge).forEach((edge) => edgeSourceIds(edge).forEach((source) => {
    outgoingCount.set(source, (outgoingCount.get(source) || 0) + 1);
  }));
  outgoingCount.forEach((count, source) => {
    if (count > 1) errors.push(`"${source}" has more than one outgoing edge.`);
  });
  // Error edge rules: at most one per node, no self-loops, target must exist.
  const errorEdgesBySource = new Map();
  edges.forEach((edge) => {
    if (edge.level === 'error') {
      const source = edge.from;
      if (errorEdgesBySource.has(source)) {
        errors.push(`"${source}" has more than one error edge.`);
      } else {
        errorEdgesBySource.set(source, edge);
      }
      if (edge.to === source) {
        errors.push(`"${source}" cannot route its own errors to itself.`);
      }
    }
  });
  const reachable = reachableFrom(entry, edges);
  nodes.filter((node) => !reachable.has(node.id)).forEach((node) => errors.push(`"${node.id}" is unreachable.`));

  // The JSON schema can only validate that an agent_name is a string. When a
  // live profile roster is available, also validate local agent references so
  // a graph cannot appear runnable while pointing at an unregistered object
  // agent. An empty roster means the roster request has not arrived yet, so
  // defer this environment-specific check rather than showing a false error.
  if (runtimeProfiles?.length) {
    const objectAgentNames = new Set(
      runtimeProfiles.filter((profile) => profile.kind === 'object').map((profile) => profile.name),
    );
    nodes.forEach((node) => {
      const config = node.config || {};
      const agentName = node.kind === 'agent'
        ? config.agent_name
        : node.kind === 'foreach' && config.mode === 'agent'
          ? config.agent_name
          : null;
      if (agentName && !objectAgentNames.has(agentName)) {
        errors.push(`Node "${node.id}" references unavailable agent "${agentName}".`);
      }
    });
  }
  return errors;
}

// Neither a rejected save nor a withheld one advances `lastSavedRef`, so
// undoing the edit behind either -- fixing the change the server refused, or
// deleting the incomplete step that was never sent -- puts the canvas back in
// step with the server. No save is queued from there to clear the chip, so
// both states have to collapse here, or the warning outlives the change that
// caused it and accuses a graph that is fine.
const UNSETTLED_SAVE_STATES = new Set(['error', 'incomplete']);
export function saveStatusWhenInSync(current) {
  return UNSETTLED_SAVE_STATES.has(current) ? 'idle' : current;
}
