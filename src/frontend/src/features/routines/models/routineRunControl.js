export const STOP_ESCALATION_MS = 5_000;

export const ROUTINE_RUN_TERMINAL_STATUSES = new Set([
  'completed', 'failed', 'paused', 'stopped', 'cancelled', 'interrupted',
]);

export const ROUTINE_RUN_ACTIVE_STATUSES = new Set([
  'enqueued', 'started', 'stopping', 'cancelling',
]);

export function isRoutineRunTerminal(status) {
  return ROUTINE_RUN_TERMINAL_STATUSES.has(status);
}

export function isRoutineRunActive(status) {
  return ROUTINE_RUN_ACTIVE_STATUSES.has(status);
}

/**
 * A control request must name the active execution attempt itself. In
 * particular, a run-history row only supplies a thread, never a safe attempt
 * request ID, so it cannot become a control target by accident.
 */
export function routineRunControlTarget(lifecycle, agentId) {
  if (lifecycle?.agent_id !== agentId
    || typeof lifecycle.thread_id !== 'string' || !lifecycle.thread_id
    || typeof lifecycle.request_id !== 'string' || !lifecycle.request_id) {
    return null;
  }
  return {
    threadId: lifecycle.thread_id,
    requestId: lifecycle.request_id,
    sessionId: typeof lifecycle.session_id === 'string' && lifecycle.session_id
      ? lifecycle.session_id
      : null,
  };
}

/**
 * UI state follows lifecycle events, never elapsed time. The only timer here
 * is the locally-recorded Stop click which reveals a cancellation escalation;
 * it cannot manufacture a stopped or cancelled outcome.
 */
export function routineRunControlMode({
  lifecycle,
  agentId,
  pendingAction = null,
  stopRequestedAtMs = null,
  nowMs = Date.now(),
}) {
  const target = routineRunControlTarget(lifecycle, agentId);
  const status = lifecycle?.status;

  if (status === 'started' && target) {
    if (pendingAction === 'cancel') return { kind: 'cancelling', target };
    if (pendingAction === 'stop') {
      return {
        kind: stopRequestedAtMs !== null && nowMs - stopRequestedAtMs >= STOP_ESCALATION_MS
          ? 'cancel'
          : 'stopping',
        target,
      };
    }
    return { kind: 'stop', target };
  }
  if (status === 'stopping' && target) {
    if (pendingAction === 'cancel') return { kind: 'cancelling', target };
    return {
      kind: stopRequestedAtMs !== null && nowMs - stopRequestedAtMs >= STOP_ESCALATION_MS
        ? 'cancel'
        : 'stopping',
      target,
    };
  }
  if (status === 'cancelling' && target) return { kind: 'cancelling', target };
  if (status === 'stopped' && target?.sessionId) return { kind: 'resume', target };
  if (status === 'enqueued') return { kind: 'queued', target: null };
  return null;
}

export function routineHistoryStatus(run, lifecycle) {
  const status = lifecycle?.thread_id === run?.thread_id ? lifecycle.status : run?.status;
  if (status === 'completed') return 'success';
  if (status === 'enqueued') return 'queued';
  if (status === 'started') return 'running';
  if (typeof status === 'string' && status) return status;
  return run?.failed ? 'failed' : 'success';
}
