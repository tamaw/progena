import { edgeSourceIds } from '../editor/graphTopology';

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

function edgeTargetIds(edge) {
  if (!edge) return [];
  if (edge.router) {
    return [
      ...Object.values(edge.branches || {}),
      ...(edge.default ? [edge.default] : []),
    ];
  }
  const targets = [];
  if (edge.to) targets.push(edge.to);
  if (edge.loop_guard?.on_false) targets.push(edge.loop_guard.on_false);
  return targets;
}

function edgesByFrom(edges) {
  const map = new Map();
  for (const edge of edges || []) {
    if (!edge) continue;
    for (const sourceId of edgeSourceIds(edge)) {
      if (!sourceId) continue;
      if (!map.has(sourceId)) map.set(sourceId, []);
      map.get(sourceId).push(edge);
    }
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
    .map((edge) => {
      if (edge.router) {
        const branchEntries = Object.entries(edge.branches || {}).sort(([a], [b]) => a.localeCompare(b));
        return {
          router: true,
          branches: branchEntries.map(([k, v]) => `${k}:${v}`),
          default: edge.default ?? null,
          level: edge.level ?? null,
          state_key: edge.state_key ?? null,
        };
      }
      return {
        to: edge.to ?? null,
        loop_guard: edge.loop_guard ?? null,
        level: edge.level ?? null,
        state_key: edge.state_key ?? null,
        default: edge.default ?? null,
      };
    })
    .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
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
      for (const edge of currentOut.get(id) || []) {
        for (const target of edgeTargetIds(edge)) {
          if (target) stack.push(target);
        }
      }
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
      for (const edge of currentOut.get(nodeId) || []) {
        for (const target of edgeTargetIds(edge)) {
          if (target) markSubtreeInvalid(target);
        }
      }
      return;
    }
    for (const edge of currentOut.get(nodeId) || []) {
      for (const target of edgeTargetIds(edge)) {
        if (target) walk(target);
      }
    }
  }

  if (current?.entry) walk(current.entry);
  return invalidated;
}

/** True if `nodeId` still has valid (non-invalidated, previously-run) details. */
export function isNodeRunValid(nodeId, invalidatedIds) {
  return Boolean(nodeId) && !invalidatedIds.has(nodeId);
}
