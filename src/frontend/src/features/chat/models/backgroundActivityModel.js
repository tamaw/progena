const TERMINAL_RUN_STATUSES = new Set(['completed', 'failed', 'cancelled', 'stopped', 'interrupted', 'error']);
const TERMINAL_TASK_STATUSES = new Set(['success', 'error', 'cancelled']);
const ASYNC_EVENT_STATUS = Object.freeze({
  async_subagent_start: 'running',
  async_subagent_pause: 'paused',
  async_subagent_cancel: 'cancelled',
});

function normalizedId(value) {
  return typeof value === 'string' && value.trim() ? value.trim() : '';
}

function timestamp(value) {
  if (Number.isFinite(value)) return value;
  const parsed = Date.parse(value || '');
  return Number.isFinite(parsed) ? parsed : null;
}

function eventTime(event, fallback) {
  return timestamp(event?.timestamp) ?? fallback;
}

function parsedResult(value) {
  if (typeof value !== 'string' || !value.trim()) return null;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function activityStatus(value, fallback = 'running') {
  const status = typeof value === 'string' ? value.trim().toLowerCase() : '';
  return status || fallback;
}

function workflowName(payload, fallback) {
  const path = typeof payload?.path === 'string' ? payload.path : '';
  const pathName = path.split('/').filter(Boolean).at(-1);
  return pathName || normalizedId(payload?.workflow_id) || fallback || 'Workflow';
}

function upsert(activities, key, next) {
  const previous = activities.get(key);
  activities.set(key, previous ? { ...previous, ...next, startedAt: previous.startedAt ?? next.startedAt } : next);
}

function applyWorkflowResult(activities, event, observedAt) {
  if (event?.eventType !== 'tool_result' || event?.metadata?.tool_name !== 'run_workflow') return;
  const payload = parsedResult(event.metadata?.result_preview);
  const runId = normalizedId(payload?.run_id);
  if (!runId) return;
  // A durable run identity is the tracker. Do not infer a run from tool success,
  // and do not promote an acknowledgement into a running/completed state.
  upsert(activities, `workflow:${runId}`, {
    key: `workflow:${runId}`,
    type: 'workflow',
    runId,
    status: 'checking',
    name: workflowName(payload, 'Workflow'),
    startedAt: observedAt,
    finishedAt: null,
    error: '',
    source: 'workflow',
  });
}

function taskRowsFromResult(event) {
  const toolName = event?.metadata?.tool_name;
  if (!['start_async_task', 'update_async_task', 'check_async_task', 'list_async_tasks', 'cancel_async_task'].includes(toolName)) {
    return [];
  }
  const payload = parsedResult(event.metadata?.result_preview);
  if (!payload) return [];
  if (Array.isArray(payload.tasks)) return payload.tasks;
  return payload.task_id ? [payload] : [];
}

function applyAsyncTask(activities, task, observedAt, threadId) {
  const taskId = normalizedId(task?.task_id);
  if (!taskId) return;
  const parentThreadId = normalizedId(task?.parent_thread_id);
  if (threadId && parentThreadId && parentThreadId !== threadId) return;
  const status = activityStatus(task?.status);
  upsert(activities, `async:${taskId}`, {
    key: `async:${taskId}`,
    type: 'async',
    taskId,
    name: normalizedId(task?.agent_name) || 'Background agent',
    status,
    startedAt: timestamp(task?.created_at) ?? observedAt,
    finishedAt: timestamp(task?.completed_at),
    error: typeof task?.last_error === 'string' ? task.last_error : '',
    summary: typeof task?.summary === 'string' ? task.summary : '',
    source: 'async',
  });
}

function applyAsyncEvent(activities, event, observedAt, threadId) {
  const eventType = event?.eventType;
  if (!eventType?.startsWith('async_subagent_')) return;
  const metadata = event.metadata || {};
  const sourceThreadId = normalizedId(metadata.thread_id);
  if (threadId && sourceThreadId && sourceThreadId !== threadId) return;
  const taskId = normalizedId(metadata.task_id);
  if (!taskId) return;
  // list_async_tasks emits lifecycle cards for every task using the caller's
  // thread ID. Only a local start can introduce an event-only task; later
  // changes require that identity or the durable parent-thread snapshot.
  if (eventType !== 'async_subagent_start' && !activities.has(`async:${taskId}`)) return;
  const status = activityStatus(metadata.status, ASYNC_EVENT_STATUS[eventType] || 'running');
  upsert(activities, `async:${taskId}`, {
    key: `async:${taskId}`,
    type: 'async',
    taskId,
    name: normalizedId(metadata.subagent_name) || 'Background agent',
    status,
    startedAt: observedAt,
    finishedAt: TERMINAL_TASK_STATUSES.has(status) ? observedAt : null,
    error: typeof metadata.error === 'string' ? metadata.error : '',
    summary: typeof metadata.result_preview === 'string' ? metadata.result_preview : '',
    source: 'async',
  });
}

function applyAsyncTaskRecords(activities, records, threadId) {
  for (const task of Array.isArray(records) ? records : []) {
    applyAsyncTask(activities, task, timestamp(task?.updated_at) ?? Date.now(), threadId);
  }
}

function allEvents(messages, liveTurn) {
  const result = [];
  for (const message of Array.isArray(messages) ? messages : []) {
    for (const event of Array.isArray(message?.streamEvents) ? message.streamEvents : []) {
      result.push({ event, fallback: timestamp(message?.timestamp) ?? 0 });
    }
  }
  for (const event of Array.isArray(liveTurn?.streamEvents) ? liveTurn.streamEvents : []) {
    result.push({ event, fallback: timestamp(liveTurn?.startedAt) ?? Date.now() });
  }
  return result;
}

function requestError(request) {
  if (request?.notFound) return 'Run was not found';
  if (!request?.error) return '';
  return typeof request.error === 'string'
    ? request.error
    : (typeof request.error.message === 'string' ? request.error.message : 'Unable to refresh run status');
}

function applyRunRows(activities, runRows, runRequests) {
  const rowsById = runRows instanceof Map ? runRows : new Map();
  const requestsById = runRequests instanceof Map ? runRequests : new Map();
  for (const activity of activities.values()) {
    if (activity.type !== 'workflow') continue;
    const row = rowsById.get(activity.runId);
    if (!row) {
      const error = requestError(requestsById.get(activity.runId));
      if (error) activities.set(activity.key, { ...activity, status: 'unavailable', error });
      continue;
    }
    const refreshError = requestError(requestsById.get(activity.runId));
    const status = refreshError ? 'unavailable' : activityStatus(row.status, activity.status);
    const startedAt = timestamp(row.started_at) ?? activity.startedAt;
    const finishedAt = timestamp(row.completed_at)
      ?? (TERMINAL_RUN_STATUSES.has(status) ? activity.finishedAt : null);
    activities.set(activity.key, {
      ...activity,
      name: normalizedId(row.name) || activity.name,
      status,
      startedAt,
      finishedAt,
      error: refreshError || activity.error,
      row,
    });
  }
}

export function isTerminalBackgroundActivity(activity) {
  if (!activity) return true;
  return activity.type === 'workflow'
    ? TERMINAL_RUN_STATUSES.has(activity.status)
    : TERMINAL_TASK_STATUSES.has(activity.status);
}

/**
 * Derive the active and settled background work belonging to one chat only.
 * Calls without a durable run/task identity are intentionally ignored.
 */
export function deriveBackgroundActivities({
  messages, liveTurn, threadId, runRows, runRequests, asyncTaskRecords,
} = {}) {
  const activities = new Map();
  for (const { event, fallback } of allEvents(messages, liveTurn)) {
    const observedAt = eventTime(event, fallback);
    applyWorkflowResult(activities, event, observedAt);
    applyAsyncEvent(activities, event, observedAt, threadId);
    for (const task of taskRowsFromResult(event)) applyAsyncTask(activities, task, observedAt, threadId);
  }
  applyAsyncTaskRecords(activities, asyncTaskRecords, threadId);
  applyRunRows(activities, runRows, runRequests);
  const all = [...activities.values()].sort((left, right) => (right.startedAt || 0) - (left.startedAt || 0));
  return {
    active: all.filter((activity) => !isTerminalBackgroundActivity(activity)),
    history: all.filter(isTerminalBackgroundActivity),
  };
}

export function backgroundRunIds(messages, liveTurn) {
  const { active, history } = deriveBackgroundActivities({ messages, liveTurn });
  return [...new Set([...active, ...history]
    .filter((activity) => activity.type === 'workflow')
    .map((activity) => activity.runId))];
}
