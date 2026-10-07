// Routine run identity and timing helpers. Live state arrives through SSE.

/** A short unique id for one Run click -- becomes the trailing segment of
 * the run's thread_id. Not a security token, just a discovery key, so a
 * plain fallback (no `crypto.randomUUID`) is fine. */
export function randomSessionId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID().replace(/-/g, '');
  }
  return `${Date.now().toString(16)}${Math.random().toString(16).slice(2, 10)}`;
}

/** Splits a `routine:<routine_id>:<revision_id>:<run_id>` thread_id --
 * mirrors the backend's `_parse_routine_thread_id` (subagent.py) exactly,
 * including its `'unpinned'` sentinel for a run against the live (not
 * revision-pinned) document. Used to find the revision a run's cached
 * timing/token/output data was executed against, so it can be diffed
 * against the current draft graph for staleness (see
 * lib/routineGraphDiff.js's diffRoutineGraphs). Returns `null` for
 * anything that doesn't parse, rather than throwing -- called on
 * whatever's in `runState.thread_id` at any point in the run lifecycle. */
export function parseRunThreadId(threadId) {
  if (typeof threadId !== 'string') return null;
  const parts = threadId.split(':');
  if (parts.length !== 4 || parts[0] !== 'routine') return null;
  const [, routineId, revisionId] = parts;
  return { routineId, revisionId: revisionId === 'unpinned' ? null : revisionId };
}

/** Finds the run whose thread_id was minted for `sessionId` among the
 * `/runs` list's entries (each `routine:<id>:<revision>:<run_id>`) --
 * `run_id` is exactly the trailing segment, set to `sessionId` by
 * RoutineGraphEditor.jsx's startRun POST body. */
export function findRunBySessionId(runs, sessionId) {
  if (!sessionId) return null;
  return (runs || []).find((run) => run.run_id === sessionId) || null;
}

/** Total executions recorded so far, across every node -- a loop node
 * contributes one entry per iteration (see factory.py's run_meta reducer).
 * Useful for summaries; lifecycle events determine completion and pauses. */
export function totalRunMetaEntries(runMeta) {
  return Object.values(runMeta || {}).reduce((sum, entries) => sum + (entries?.length || 0), 0);
}

/** The latest moment any node is known to have finished (started_at +
 * duration_ms, maximized over every entry) -- the best available estimate
 * for "when did whatever's running right now actually start", since
 * there's no real per-node start signal (see module doc above). `null` when
 * run_meta is empty (nothing has completed yet). */
export function latestCompletionMs(runMeta) {
  let latest = null;
  Object.values(runMeta || {}).forEach((entries) => {
    (entries || []).forEach((entry) => {
      const startedMs = Date.parse(entry?.started_at);
      if (Number.isNaN(startedMs)) return;
      const finishedMs = startedMs + (entry?.duration_ms || 0);
      if (latest === null || finishedMs > latest) latest = finishedMs;
    });
  });
  return latest;
}
