import { predecessorsOf, nodeSuccessors } from './graphTopology';

// The sub-header under a step's title once run data exists for it -- a
// finalized "Xs · N tok" once its own checkpoint has landed, or a ticking
// "estimated live" timer while it's presumed still executing. Renders
// nothing at all otherwise (see StepCard), not a placeholder row.
// Given a node's own run_meta entries and the iteration currently selected
// for its enclosing loop (see buildLoopMembership/LoopBlock), returns the
// entry belonging to that iteration -- bucketed by each entry's `step`
// (factory.py's _instrument_node_callable) against the guard node's own
// per-iteration step, since a loop-body node can run more than once and
// "just take the last entry" would otherwise always show the latest
// iteration regardless of which one is selected. `iterationIndex` null
// means "latest" (today's default, no override).
export function pickEntryForIteration(entries, iterationIndex, guardEntries) {
  if (!entries?.length) return undefined;
  if (iterationIndex == null) return entries[entries.length - 1];
  const upper = guardEntries?.[iterationIndex - 1]?.step ?? Infinity;
  const lower = guardEntries?.[iterationIndex - 2]?.step ?? -Infinity;
  const inWindow = entries.filter((entry) => typeof entry.step === 'number' && entry.step > lower && entry.step <= upper);
  return inWindow[inWindow.length - 1] ?? entries[entries.length - 1];
}

// Node kinds with a real external side effect: a live agent/routine
// delegation, an A2A call, or a human approval gate. Everything else
// (eval/condition/switch/split/noop) is pure/deterministic -- re-running
// it has no observable consequence outside this graph's own state.
const SIDE_EFFECTING_KINDS = new Set(['agent', 'routine', 'a2a', 'human']);

// Whether "run to here" for `targetNodeId` would, for the first time this
// interactive session, execute a node with a real side effect --
// `interrupt_after=[targetNodeId]` (see subagent.py) means everything
// UP TO AND INCLUDING the target runs, so both its predecessors and the
// clicked node itself matter here. A node that already carries a run_meta
// entry this session already had its side effect happen once and won't
// happen again on this click (LangGraph resumes past whatever's already
// checkpointed, see subagent.py's `_run`) -- only a genuinely new
// execution needs a confirmation.
export function nodeRequiresRunConfirmation(targetNodeId, nodes, edges, runMeta) {
  const nodesById = new Map((nodes || []).map((node) => [node.id, node]));
  const completed = new Set(Object.keys(runMeta || {}).filter((id) => (runMeta[id] || []).length > 0));
  for (const id of [...predecessorsOf(targetNodeId, edges), targetNodeId]) {
    if (completed.has(id)) continue;
    if (SIDE_EFFECTING_KINDS.has(nodesById.get(id)?.kind)) return true;
  }
  return false;
}

// Best-effort "which node(s) are probably executing right now" purely from
// static graph shape plus which node ids already have a run_meta entry --
// see lib/routineRunPolling.js's module doc for why this has to be
// *inferred* client-side rather than read off a real backend signal.
// `completedNodeIds` empty means the run just started: only `entry` can be
// active. A branch/fan-out can legitimately put more than one node id in
// the returned set -- we genuinely don't know which branch fired yet, and
// ticking every candidate is more honest than guessing one. A node already
// in `completedNodeIds` is never re-offered as active even if a loop will
// run it again -- this is a known, documented limitation (see
// RoutineGraphEditor.jsx's useRoutineRunPolling), not a soundness claim.
export function deriveActiveNodeIds(nodes, edges, entry, completedNodeIds) {
  const nodeIds = new Set((nodes || []).map((node) => node.id));
  if (!entry || !nodeIds.has(entry)) return new Set();
  if (!completedNodeIds || completedNodeIds.size === 0) return new Set([entry]);
  const active = new Set();
  completedNodeIds.forEach((id) => {
    if (!nodeIds.has(id)) return;
    nodeSuccessors(id, edges).forEach((targetId) => {
      if (nodeIds.has(targetId) && !completedNodeIds.has(targetId)) active.add(targetId);
    });
  });
  return active;
}
export function formatSeconds(ms) {
  const seconds = ms / 1000;
  return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)}s`;
}
