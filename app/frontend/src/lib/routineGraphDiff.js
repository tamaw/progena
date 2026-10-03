/**
 * Structural diff between two routine graph documents (same shape as
 * `serializeGraph` in graphEdits.js: `{ entry, nodes, edges, ... }`).
 *
 * Used to invalidate a node's cached run details (timing/tokens/IO) when the
 * graph changes: walk from `entry` in the *current* graph, comparing each
 * node (and its outgoing edge set) against the *baseline* graph a prior run
 * executed against, node by node in reachability order. The first node whose
 * own content or outgoing edges differ -- or that didn't exist in the
 * baseline at all -- is "divergent"; it and everything reachable from it in
 * the current graph is invalidated. Everything visited before that point is
 * untouched, so run details for unaffected upstream nodes stay valid across
 * edits below them.
 *
 * A node id revisited via a loop back-edge is not re-walked (guards against
 * infinite recursion); its validity was already decided on first visit.
 */

function edgesByFrom(edges) {
  const map = new Map();
  for (const edge of edges || []) {
    if (!edge || typeof edge.from !== 'string') continue;
    if (!map.has(edge.from)) map.set(edge.from, []);
    map.get(edge.from).push(edge);
  }
  return map;
}

function nodeSignature(node) {
  if (!node) return null;
  return JSON.stringify({
    kind: node.kind,
    label: node.label,
    description: node.description || '',
    config: node.config || {},
  });
}

// Edge identity ignores `from` (implied by the map key) and any incidental
// field ordering; only the routing-relevant fields count.
function edgeSetSignature(edges) {
  const sorted = (edges || [])
    .map((edge) => ({
      to: edge.to,
      router: edge.router ?? null,
      condition: edge.condition ?? null,
      loop_guard: edge.loop_guard ?? null,
      default: Boolean(edge.default),
    }))
    .sort((a, b) => `${a.to}\u0000${a.router}\u0000${a.condition}`.localeCompare(`${b.to}\u0000${b.router}\u0000${b.condition}`));
  return JSON.stringify(sorted);
}

/**
 * @param {{entry?: string, nodes?: Array, edges?: Array}} baseline graph the prior run executed against
 * @param {{entry?: string, nodes?: Array, edges?: Array}} current graph in the editor now
 * @returns {Set<string>} node ids (in `current`) invalidated relative to `baseline`
 */
export function diffRoutineGraphs(baseline, current) {
  const baselineNodes = new Map((baseline?.nodes || []).map((node) => [node.id, node]));
  const currentNodes = new Map((current?.nodes || []).map((node) => [node.id, node]));
  const baselineOut = edgesByFrom(baseline?.edges);
  const currentOut = edgesByFrom(current?.edges);

  const invalidated = new Set();
  const visited = new Set();

  function markSubtreeInvalid(nodeId) {
    const stack = [nodeId];
    while (stack.length) {
      const id = stack.pop();
      if (invalidated.has(id) || !currentNodes.has(id)) continue;
      invalidated.add(id);
      for (const edge of currentOut.get(id) || []) stack.push(edge.to);
    }
  }

  function walk(nodeId) {
    if (visited.has(nodeId) || invalidated.has(nodeId) || !currentNodes.has(nodeId)) return;
    visited.add(nodeId);
    const baseNode = baselineNodes.get(nodeId);
    const curNode = currentNodes.get(nodeId);
    if (!baseNode || nodeSignature(baseNode) !== nodeSignature(curNode)) {
      markSubtreeInvalid(nodeId);
      return;
    }
    if (edgeSetSignature(baselineOut.get(nodeId)) !== edgeSetSignature(currentOut.get(nodeId))) {
      // This node itself is unchanged, but where it routes to has moved --
      // whatever it now points at has no verified relationship to what ran
      // last time, so everything downstream (per the *current* topology) is
      // invalidated, even though this node's own run details stay valid.
      for (const edge of currentOut.get(nodeId) || []) markSubtreeInvalid(edge.to);
      return;
    }
    for (const edge of currentOut.get(nodeId) || []) walk(edge.to);
  }

  if (current?.entry) walk(current.entry);
  return invalidated;
}

/** True if `nodeId` still has valid (non-invalidated, previously-run) details. */
export function isNodeRunValid(nodeId, invalidatedIds) {
  return Boolean(nodeId) && !invalidatedIds.has(nodeId);
}
