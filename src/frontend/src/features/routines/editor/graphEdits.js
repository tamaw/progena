import { GRAPH_KIND_LABELS, titleCase } from './graphNodeKinds';
import { edgeSourceIds, forkJoin, reachableFrom } from './graphTopology';

export function uniqueNodeId(kind, nodes) {
  const ids = new Set(nodes.map((node) => node.id));
  if (!ids.has(kind)) return kind;
  let i = 1;
  while (ids.has(`${kind}-${i}`)) i += 1;
  return `${kind}-${i}`;
}

function uniqueBranchValue(branches) {
  const used = new Set(Object.keys(branches || {}));
  let i = 1;
  while (used.has(`case-${i}`)) i += 1;
  return `case-${i}`;
}

export function makeNode(kind) {
  const config = {
    prompt: { prompt_template: '' },
    eval: { expression: '' },
    python: { code: '', input_map: {}, input_files: {}, output_files: {} },
    condition: { expression: '' },
    switch: { expression: '' },
    split: {},
    classify: { rubric_prompt: '', response_schema: {} },
    agent: { agent_name: '', prompt_template: '' },
    a2a: { agent_name: '', prompt_template: '' },
    human: { message: '' },
    foreach: { mode: 'send', items_from: '', item_key: 'item' },
    reduce: { source_node: '', operation: 'collect' },
    routine: { routine_name: '', input_map: {}, output_map: {} },
    noop: {},
  }[kind] || {};
  return { id: '', kind, label: GRAPH_KIND_LABELS[kind] || titleCase(kind), description: '', config };
}

// The editor's graph contract does not allow a separate output key: runtime
// writes are indexed by node id. Normalize on ingestion as well as when
// serializing so live inspector state matches the persisted graph immediately.
export function normalizeGraphNodes(nodes) {
  return (nodes || []).map((node) => ({ ...node, output_key: node.id }));
}

// Drop every trace of `removed`: edges into them go, edges out of them go, and
// a join that loses a source follows the same fan_in bookkeeping as anywhere
// else. A router whose own source is gone disappears with it.
function pruneRemovedNodes(edges, removed) {
  if (!removed.size) return edges;
  return edges.map((edge) => {
    const sources = edgeSourceIds(edge).filter((source) => !removed.has(source));
    if (!sources.length) return null;
    if (edge.router) return edge;
    if (removed.has(edge.to)) return null;
    return withoutJoinSources(edge, removed);
  }).filter(Boolean);
}

// Mirrors deleteNode's fan_in bookkeeping: a join stays a fan_in while two or
// more sources remain, collapses to a plain edge at one, and goes away at none.
function withoutJoinSources(edge, drop) {
  const sources = edgeSourceIds(edge).filter((source) => !drop.has(source));
  if (sources.length === edgeSourceIds(edge).length) return edge;
  if (sources.length >= 2) return { ...edge, from: sources };
  if (sources.length === 1) return { from: sources[0], to: edge.to, level: 'normal' };
  return null;
}

// A new lane joins wherever its siblings already join, so it comes out shaped
// like one that existed when the join was built. Routing it through the
// existing fan_in is also what earns the join `defer=True` at compile time
// (the factory derives that from being a fan_in target), so the join still
// fires on whichever single branch actually ran.
export function addForkLane(edges, sourceId, laneId, isDefault = false) {
  const router = edges.find((edge) => edge.from === sourceId && edge.router);
  if (!router) return edges;
  const join = forkJoin(edges, router);
  return edges.map((edge) => {
    if (edge === router) {
      return isDefault
        ? { ...edge, default: laneId }
        : { ...edge, branches: { ...edge.branches, [uniqueBranchValue(edge.branches)]: laneId } };
    }
    if (join && edge === join.edge) return { ...edge, from: [...edgeSourceIds(edge), laneId] };
    return edge;
  });
}

// Dropping a lane takes the whole lane with it: the steps it owned are deleted
// outright rather than left floating in "Unreachable steps", and they come off
// the join's source list on the way out. Ownership is decided by reachability
// -- whatever the graph could reach before the branch was cut and cannot reach
// after -- so a lane holding a nested fork goes as a unit, while a step some
// other lane also points at correctly stays.
function dropForkLane(edges, entry, sourceId, strip) {
  const router = edges.find((edge) => edge.from === sourceId && edge.router);
  if (!router) return { edges, removedNodeIds: [] };
  const cut = edges.map((edge) => (edge === router ? strip(edge) : edge)).filter(Boolean);
  const before = reachableFrom(entry, edges);
  const after = reachableFrom(entry, cut);
  const removed = new Set([...before].filter((id) => !after.has(id)));
  return { edges: pruneRemovedNodes(cut, removed), removedNodeIds: [...removed] };
}

export function removeForkBranch(edges, entry, sourceId, value) {
  return dropForkLane(edges, entry, sourceId, (edge) => {
    const branches = { ...edge.branches };
    delete branches[value];
    if (!Object.keys(branches).length && !edge.default) return null;
    if (!Object.keys(branches).length) return { from: edge.from, to: edge.default, level: 'normal' };
    return { ...edge, branches };
  });
}

export function removeForkDefault(edges, entry, sourceId) {
  return dropForkLane(edges, entry, sourceId, (edge) => {
    const { default: _drop, ...rest } = edge;
    if (!Object.keys(rest.branches || {}).length) return null;
    return rest;
  });
}

// Serialize the working nodes/edges/meta into the backend graph document
// shape. `output_key` always mirrors `id` -- it isn't independently editable.
export function serializeGraph(nodes, edges, meta) {
  return {
    status: meta.status || 'Draft',
    version: meta.version || '2.0.0',
    summary: meta.summary || '',
    entry: meta.entry || nodes[0]?.id || null,
    nodes: normalizeGraphNodes(nodes).map((node) => ({
      id: node.id,
      kind: node.kind,
      label: node.label,
      description: node.description || '',
      output_key: node.id,
      config: node.config || {},
    })),
    edges,
    policy_id: meta.policy_id || 'default',
    input_schema: meta.input_schema || null,
    output_schema: meta.output_schema || null,
    $defs: meta.$defs || {},
  };
}

// Persisted JSON documents come back from Postgres JSONB with object keys in a
// database-defined order. Canonicalize object keys before comparing graph
// signatures; array order remains significant for nodes, edges, and schema
// lists.
export function stableStringify(value) {
  const canonicalize = (item) => {
    if (Array.isArray(item)) return item.map(canonicalize);
    if (item && typeof item === 'object') {
      return Object.fromEntries(
        Object.keys(item).sort().map((key) => [key, canonicalize(item[key])]),
      );
    }
    return item;
  };
  return JSON.stringify(canonicalize(value));
}
