import { createSelector } from '@reduxjs/toolkit';
import { withQuery } from '../../store/http';
import {
  addKeyedRequestLifecycle,
  completeKeyedRequest,
  createAcknowledgedThunk,
  requestAcknowledged,
  selectKeyedRequest,
} from '../../store/async';
import { realtimeClientId } from '../../realtime/realtimeClient';

export const RUN_KINDS = ['routine', 'workflow', 'schedule'];
export const RUN_STATUSES = ['queued', 'running', 'paused', 'completed', 'failed', 'cancelled', 'stopped', 'interrupted'];
export const RUN_PAGE_SIZE = 50;
export const RUN_PERIODS = Object.freeze({ hour: 3_600_000, day: 86_400_000, week: 604_800_000, month: 2_592_000_000 });

export function runMatchesPeriod(row, period, now = Date.now()) {
  if (!Object.hasOwn(RUN_PERIODS, period)) return true;
  const started = Date.parse(row?.started_at || '');
  return Number.isFinite(started) && started >= now - RUN_PERIODS[period];
}

export function normalizeRunsQuery({ kind = '', status = '', q = '', cursor = null, period = '' } = {}) {
  return {
    kind: RUN_KINDS.includes(kind) ? kind : '',
    status: typeof status === 'string' ? status.trim().toLowerCase() : '',
    q: typeof q === 'string' ? q.trim() : '',
    cursor: typeof cursor === 'string' && cursor ? cursor : null,
    period: Object.hasOwn(RUN_PERIODS, period) ? period : '',
  };
}

export function runsQueryKey(query) {
  const normalized = normalizeRunsQuery(query);
  return JSON.stringify([normalized.kind, normalized.status, normalized.q, normalized.cursor, normalized.period]);
}

export function traceKey(kind, runId) {
  return kind && runId ? `${kind}:${runId}` : null;
}

export const initialState = {
  runs: {
    snapshots: {},
    requests: {},
    traces: {},
    traceRequests: {},
  },
};

export const fetchRuns = createAcknowledgedThunk(
  'uiData/fetchRuns',
  ({ runsUrl, ...query }, { requestId }) => {
    const normalized = normalizeRunsQuery(query);
    return requestAcknowledged(withQuery(runsUrl, {
      ...(normalized.kind ? { kind: normalized.kind } : {}),
      ...(normalized.status ? { status: normalized.status } : {}),
      ...(normalized.q ? { q: normalized.q } : {}),
      ...(normalized.cursor ? { cursor: normalized.cursor } : {}),
      ...(normalized.period ? { period: normalized.period } : {}),
    }), { requestId });
  },
);

export const fetchRunTrace = createAcknowledgedThunk(
  'uiData/fetchRunTrace',
  ({ runsUrl, kind, runId }, { requestId }) => requestAcknowledged(
    withQuery(`${runsUrl}/${encodeURIComponent(kind)}/${encodeURIComponent(runId)}/trace`, { client_id: realtimeClientId }),
    { requestId },
  ),
);

function rowKey(row) {
  return traceKey(row?.kind, row?.run_id);
}

function rowMatchesQuery(row, query) {
  if (!row || !rowKey(row)) return false;
  if (query.kind && row.kind !== query.kind) return false;
  if (query.status && String(row.status || '').toLowerCase() !== query.status) return false;
  if (!runMatchesPeriod(row, query.period)) return false;
  return !query.q || [row.name, row.trigger, row.run_id]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(query.q.toLowerCase()));
}

function rowSortKey(row) {
  return [row?.started_at || '', row?.kind || '', row?.run_id || ''];
}

function compareRows(left, right) {
  const leftKey = rowSortKey(left);
  const rightKey = rowSortKey(right);
  return rightKey[0].localeCompare(leftKey[0])
    || rightKey[1].localeCompare(leftKey[1])
    || rightKey[2].localeCompare(leftKey[2]);
}

function cursorKey(cursor) {
  if (!cursor) return null;
  try {
    const encoded = cursor.replace(/-/g, '+').replace(/_/g, '/');
    const decoded = JSON.parse(atob(`${encoded}${'='.repeat((-encoded.length) % 4)}`));
    return Array.isArray(decoded) && decoded.length === 3 && decoded.every((value) => typeof value === 'string')
      ? decoded
      : null;
  } catch {
    return null;
  }
}

function isBeforeCursor(row, cursor) {
  const boundary = cursorKey(cursor);
  if (!boundary) return true;
  const key = rowSortKey(row);
  return key[0] < boundary[0]
    || (key[0] === boundary[0] && (key[1] < boundary[1]
      || (key[1] === boundary[1] && key[2] < boundary[2])));
}

function mergeLiveRows(snapshot, changes, query) {
  const byKey = new Map((snapshot.runs || []).map((row) => [rowKey(row), row]));
  for (const [key, row] of Object.entries(changes.rows || {})) {
    const existing = byKey.has(key);
    if (changes.deleted?.[key]) {
      byKey.delete(key);
    } else if (!query.cursor || existing) {
      if (rowMatchesQuery(row, query) && isBeforeCursor(row, query.cursor)) {
        byKey.set(key, row);
      } else {
        byKey.delete(key);
      }
    }
  }
  for (const key of Object.keys(changes.deleted || {})) byKey.delete(key);
  const limit = Math.max(snapshot.page_size || RUN_PAGE_SIZE, snapshot.runs?.length || 0);
  return [...byKey.values()].sort(compareRows).slice(0, limit);
}

function changesAffectSnapshot(changes, query) {
  if (Object.keys(changes.deleted || {}).length) return true;
  return Object.values(changes.rows || {}).some((row) => (
    rowMatchesQuery(row, query) && isBeforeCursor(row, query.cursor)
  ));
}

function markSnapshotsStale(state, row) {
  for (const snapshot of Object.values(state.runs.snapshots)) {
    const query = normalizeRunsQuery(snapshot.query);
    const key = rowKey(row);
    const existingIndex = snapshot.runs.findIndex((item) => rowKey(item) === key);
    const matches = rowMatchesQuery(row, query) && isBeforeCursor(row, query.cursor);
    if (existingIndex >= 0) {
      const rows = snapshot.runs.filter((item) => rowKey(item) !== key);
      if (matches) rows.push(row);
      snapshot.runs = rows.sort(compareRows).slice(0, snapshot.page_size || RUN_PAGE_SIZE);
      snapshot.stale = true;
    } else if (!query.cursor && matches) {
      snapshot.runs = [...snapshot.runs, row]
        .sort(compareRows)
        .slice(0, snapshot.page_size || RUN_PAGE_SIZE);
      snapshot.stale = true;
    }
  }
}

export const eventHandlers = {
  'runs.snapshot': (state, payload) => {
    const entry = Object.entries(state.runs.requests)
      .find(([, request]) => request?.requestId === payload.request_id);
    if (!entry) return;
    const [key, request] = entry;
    const queryParts = JSON.parse(key);
    const query = {
      kind: queryParts[0],
      status: queryParts[1],
      q: queryParts[2],
      cursor: queryParts[3],
      period: queryParts[4],
    };
    const changes = request.changes || {};
    const snapshot = {
      query,
      runs: Array.isArray(payload.runs) ? payload.runs : [],
      next_cursor: payload.next_cursor || null,
      page_size: Array.isArray(payload.runs) ? payload.runs.length || RUN_PAGE_SIZE : RUN_PAGE_SIZE,
      stale: false,
    };
    snapshot.stale = changesAffectSnapshot(changes, query);
    snapshot.runs = mergeLiveRows(snapshot, changes, query);
    state.runs.snapshots[key] = snapshot;
    completeKeyedRequest(state.runs.requests, key, payload.request_id);
  },
  'run.updated': (state, payload) => {
    const row = payload.run;
    const key = rowKey(row);
    if (!key) return;
    for (const request of Object.values(state.runs.requests)) {
      if (request?.loading) {
        request.changes ||= { rows: {}, deleted: {} };
        request.changes.rows[key] = row;
        delete request.changes.deleted[key];
      }
    }
    markSnapshotsStale(state, row);
  },
  'run.deleted': (state, payload) => {
    const key = traceKey(payload.kind, payload.run_id);
    if (!key) return;
    for (const request of Object.values(state.runs.requests)) {
      if (request?.loading) {
        request.changes ||= { rows: {}, deleted: {} };
        request.changes.deleted[key] = true;
        delete request.changes.rows[key];
      }
    }
    for (const snapshot of Object.values(state.runs.snapshots)) {
      snapshot.runs = snapshot.runs.filter((row) => rowKey(row) !== key);
      // A deleted item before a cursor can pull a new item into this page.
      // The next correlated snapshot reconciles that otherwise invisible boundary.
      snapshot.stale = true;
    }
    delete state.runs.traces[key];
  },
  'run.trace.loaded': (state, payload) => {
    const key = traceKey(payload.trace?.kind, payload.trace?.run_id);
    const request = key ? state.runs.traceRequests[key] : null;
    if (!key || !request?.requestId || payload.request_id !== request.requestId) return;
    state.runs.traces[key] = payload.trace;
    completeKeyedRequest(state.runs.traceRequests, key, payload.request_id);
  },
  'run.trace.updated': (state, payload) => {
    const key = traceKey(payload.trace?.kind, payload.trace?.run_id);
    if (!key) return;
    // Targeted trace events are ordered on the canonical stream. A subscription
    // update uses its own request ID, so it must not be rejected as a stale GET.
    state.runs.traces[key] = payload.trace;
    const request = state.runs.traceRequests[key];
    if (request?.loading) {
      request.loading = false;
      request.error = null;
      request.requestId = null;
    }
  },
};

export function buildExtraReducers(builder) {
  addKeyedRequestLifecycle(builder, fetchRuns, {
    selectRequests: (state) => state.runs.requests,
    keyFromArgs: (args) => runsQueryKey(args),
    errorMessage: 'Failed to load runs',
  });
  addKeyedRequestLifecycle(builder, fetchRunTrace, {
    selectRequests: (state) => state.runs.traceRequests,
    keyFromArgs: (args) => traceKey(args.kind, args.runId),
    errorMessage: 'Failed to load run trace',
  });
}

const selectRunsDomain = (state) => state.uiData.runs;
export const selectRunsSnapshot = createSelector(
  [selectRunsDomain, (_state, query) => runsQueryKey(query)],
  (runs, key) => runs.snapshots[key] || null,
);
export const selectRunsRequest = createSelector(
  [selectRunsDomain, (_state, query) => runsQueryKey(query)],
  (runs, key) => selectKeyedRequest(runs.requests, key),
);
export const selectRunsQueryState = createSelector(
  [selectRunsSnapshot, selectRunsRequest],
  (snapshot, request) => ({ snapshot, ...request }),
);
export const selectRunTrace = createSelector(
  [selectRunsDomain, (_state, kind, runId) => traceKey(kind, runId)],
  (runs, key) => key ? runs.traces[key] || null : null,
);
export const selectRunTraceRequest = createSelector(
  [selectRunsDomain, (_state, kind, runId) => traceKey(kind, runId)],
  (runs, key) => selectKeyedRequest(runs.traceRequests, key),
);
