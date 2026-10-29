import { afterEach, describe, expect, it, vi } from 'vitest';
import reducer from '@frontend/store/uiDataSlice';
import { applyUiEvent } from '@frontend/store/uiEvents';
import { compareRunRows, fetchRuns, normalizeRunPeriodParam, normalizeRunSelection, normalizeRunsQuery, runMatchesPeriod, runSelectionParam, runsQueryKey } from '@frontend/features/runs/state';
import { mergeRunPages } from '@frontend/features/runs/pageModel';
import { runDurationMs } from '@frontend/features/runs/runFormat';

const query = { runsUrl: '/api/ui/runs', kind: '', status: '', q: '', cursor: null };
const row = (runId, startedAt = '2026-10-28T10:00:00Z') => ({
  kind: 'workflow', run_id: runId, name: runId, trigger: 'manual', status: 'running',
  started_at: startedAt, completed_at: null, tokens: 10,
});

function pending(state, requestId, requestQuery = query) {
  return reducer(state, fetchRuns.pending(requestId, requestQuery));
}

afterEach(() => vi.restoreAllMocks());

describe('runs state', () => {
  it('accepts only the correlated snapshot and preserves a newer live row', () => {
    let state = pending(undefined, 'first');
    state = pending(state, 'second');
    state = reducer(state, applyUiEvent({ type: 'run.updated', payload: { run: row('new', '2026-10-28T11:00:00Z') } }));
    state = reducer(state, applyUiEvent({ type: 'runs.snapshot', payload: { request_id: 'first', runs: [row('old')], next_cursor: null } }));
    expect(state.runs.snapshots[runsQueryKey(query)]).toBeUndefined();

    state = reducer(state, applyUiEvent({ type: 'runs.snapshot', payload: { request_id: 'second', runs: [row('old')], next_cursor: null } }));
    const snapshot = state.runs.snapshots[runsQueryKey(query)];
    expect(snapshot.runs.map((item) => item.run_id)).toEqual(['new', 'old']);
    expect(snapshot.stale).toBe(true);
  });

  it('replaces the old window with the authoritative reconnect snapshot after deletes and status changes', () => {
    let state = pending(undefined, 'initial');
    state = reducer(state, applyUiEvent({ type: 'runs.snapshot', payload: {
      request_id: 'initial',
      runs: [row('deleted'), row('status')],
      next_cursor: 'after-status',
    } }));

    state = pending(state, 'reconnect', { ...query, resync: true });
    state = reducer(state, applyUiEvent({ type: 'runs.snapshot', payload: {
      request_id: 'reconnect',
      runs: [{ ...row('status'), status: 'completed', completed_at: '2026-10-28T10:05:00Z' }],
      next_cursor: null,
    } }));

    const snapshot = state.runs.snapshots[runsQueryKey(query)];
    expect(snapshot.runs).toEqual([
      expect.objectContaining({ run_id: 'status', status: 'completed', completed_at: '2026-10-28T10:05:00Z' }),
    ]);
    expect(snapshot.stale).toBe(false);
  });

  it('does not retain a run that no longer matches a filtered query after reconnect', () => {
    const runningQuery = { ...query, status: 'running' };
    let state = pending(undefined, 'initial', runningQuery);
    state = reducer(state, applyUiEvent({ type: 'runs.snapshot', payload: {
      request_id: 'initial',
      runs: [row('changed-status')],
      next_cursor: null,
    } }));

    state = pending(state, 'reconnect', { ...runningQuery, resync: true });
    state = reducer(state, applyUiEvent({ type: 'runs.snapshot', payload: {
      request_id: 'reconnect',
      runs: [],
      next_cursor: null,
    } }));

    const snapshot = state.runs.snapshots[runsQueryKey(runningQuery)];
    expect(snapshot.runs).toEqual([]);
    expect(snapshot.stale).toBe(false);
  });

  it('applies canonical updates received during reconnect hydration without retaining its old window', () => {
    let state = pending(undefined, 'initial');
    state = reducer(state, applyUiEvent({ type: 'runs.snapshot', payload: {
      request_id: 'initial',
      runs: [row('old-only'), row('updated'), row('deleted')],
      next_cursor: null,
    } }));

    state = pending(state, 'reconnect', { ...query, resync: true });
    state = reducer(state, applyUiEvent({ type: 'run.updated', payload: {
      run: { ...row('updated'), status: 'completed', completed_at: '2026-10-28T10:05:00Z' },
    } }));
    state = reducer(state, applyUiEvent({ type: 'run.deleted', payload: {
      kind: 'workflow',
      run_id: 'deleted',
    } }));
    state = reducer(state, applyUiEvent({ type: 'runs.snapshot', payload: {
      request_id: 'reconnect',
      runs: [row('updated'), row('deleted')],
      next_cursor: null,
    } }));

    const snapshot = state.runs.snapshots[runsQueryKey(query)];
    expect(snapshot.runs).toEqual([
      expect.objectContaining({ run_id: 'updated', status: 'completed', completed_at: '2026-10-28T10:05:00Z' }),
    ]);
    expect(snapshot.stale).toBe(true);
  });

  it('keeps a continuous loaded window when a live arrival shifts a cursor boundary and the first page refreshes', () => {
    const pageTwo = { ...query, cursor: 'after-b' };
    let state = pending(undefined, 'first', query);
    state = reducer(state, applyUiEvent({ type: 'runs.snapshot', payload: {
      request_id: 'first', runs: [row('a', '2026-10-28T10:00:00Z'), row('b', '2026-10-28T09:00:00Z')], next_cursor: 'after-b',
    } }));
    state = pending(state, 'second', pageTwo);
    state = reducer(state, applyUiEvent({ type: 'runs.snapshot', payload: {
      request_id: 'second', runs: [row('c', '2026-10-28T08:00:00Z'), row('d', '2026-10-28T07:00:00Z')], next_cursor: 'after-d',
    } }));

    state = reducer(state, applyUiEvent({ type: 'run.updated', payload: { run: row('new', '2026-10-28T11:00:00Z') } }));
    state = pending(state, 'refresh', query);
    state = reducer(state, applyUiEvent({ type: 'runs.snapshot', payload: {
      request_id: 'refresh', runs: [row('new', '2026-10-28T11:00:00Z'), row('a', '2026-10-28T10:00:00Z')], next_cursor: 'after-a',
    } }));

    const firstPage = state.runs.snapshots[runsQueryKey(query)];
    const secondPage = state.runs.snapshots[runsQueryKey(pageTwo)];
    expect(firstPage.runs.map((item) => item.run_id)).toEqual(['new', 'a', 'b']);
    expect(secondPage.runs.map((item) => item.run_id)).toEqual(['c', 'd']);
    expect([...firstPage.runs, ...secondPage.runs].map((item) => item.run_id)).toEqual(['new', 'a', 'b', 'c', 'd']);
    expect(secondPage.next_cursor).toBe('after-d');
    expect(firstPage.stale).toBe(false);
    state = reducer(state, applyUiEvent({ type: 'run.updated', payload: { run: row('d', '2026-10-28T12:00:00Z') } }));
    expect(mergeRunPages([
      state.runs.snapshots[runsQueryKey(query)],
      state.runs.snapshots[runsQueryKey(pageTwo)],
    ], (left, right) => compareRunRows(left, right, 'newest')).map((item) => item.run_id)).toEqual(['d', 'new', 'a', 'b', 'c']);

  });

  it('keeps a live update for every in-flight query rather than clearing it with the first snapshot', () => {
    const searchQuery = { ...query, q: 'target' };
    let state = pending(undefined, 'all');
    state = pending(state, 'search', searchQuery);
    const completed = { ...row('target'), status: 'completed', tokens: 30, completed_at: '2026-10-28T10:01:00Z' };
    state = reducer(state, applyUiEvent({ type: 'run.updated', payload: { run: completed } }));
    state = reducer(state, applyUiEvent({ type: 'runs.snapshot', payload: { request_id: 'all', runs: [row('target')], next_cursor: null } }));
    state = reducer(state, applyUiEvent({ type: 'runs.snapshot', payload: { request_id: 'search', runs: [row('target')], next_cursor: null } }));
    for (const activeQuery of [query, searchQuery]) {
      expect(state.runs.snapshots[runsQueryKey(activeQuery)].runs[0]).toMatchObject({
        run_id: 'target', status: 'completed', tokens: 30, completed_at: completed.completed_at,
      });
    }
  });

  it('matches live search updates by run ID, as the list endpoint does', () => {
    const searchQuery = { ...query, q: 'needle' };
    let state = pending(undefined, 'request', searchQuery);
    state = reducer(state, applyUiEvent({ type: 'run.updated', payload: { run: { ...row('needle-run'), name: 'Unrelated display name' } } }));
    state = reducer(state, applyUiEvent({ type: 'runs.snapshot', payload: { request_id: 'request', runs: [], next_cursor: null } }));
    expect(state.runs.snapshots[runsQueryKey(searchQuery)].runs.map((item) => item.run_id)).toEqual(['needle-run']);
  });

  it('does not optimistically insert a live row into a cursor page', () => {
    const cursor = btoa(JSON.stringify(['2026-10-28T10:00:00Z', 'workflow', 'cursor']))
      .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    const pagedQuery = { ...query, cursor };
    let state = pending(undefined, 'request', pagedQuery);
    state = reducer(state, applyUiEvent({ type: 'run.updated', payload: { run: row('later', '2026-10-28T09:00:00Z') } }));
    state = reducer(state, applyUiEvent({ type: 'runs.snapshot', payload: { request_id: 'request', runs: [row('page', '2026-10-28T09:30:00Z')], next_cursor: null } }));
    const snapshot = state.runs.snapshots[runsQueryKey(pagedQuery)];
    expect(snapshot.runs.map((item) => item.run_id)).toEqual(['page']);
    expect(snapshot.stale).toBe(true);
  });

  it('removes deleted rows and marks the bounded page for reconciliation', () => {
    let state = pending(undefined, 'request');
    state = reducer(state, applyUiEvent({ type: 'runs.snapshot', payload: { request_id: 'request', runs: [row('gone')], next_cursor: 'next' } }));
    state = reducer(state, applyUiEvent({ type: 'run.deleted', payload: { kind: 'workflow', run_id: 'gone' } }));
    const snapshot = state.runs.snapshots[runsQueryKey(query)];
    expect(snapshot.runs).toEqual([]);
    expect(snapshot.stale).toBe(true);
  });

  it('reconciles an existing live row in place without duplicating its stable run identity', () => {
    let state = pending(undefined, 'request');
    state = reducer(state, applyUiEvent({ type: 'runs.snapshot', payload: { request_id: 'request', runs: [row('inspected')], next_cursor: null } }));
    const updated = { ...row('inspected'), status: 'completed', completed_at: '2026-10-28T10:03:00Z' };
    state = reducer(state, applyUiEvent({ type: 'run.updated', payload: { run: updated } }));

    const snapshot = state.runs.snapshots[runsQueryKey(query)];
    expect(snapshot.runs).toHaveLength(1);
    expect(snapshot.runs[0]).toMatchObject(updated);
  });
});

describe('runs query state', () => {
  it('keeps global sort modes in independently cached request identities', () => {
    const newest = normalizeRunsQuery({ ...query, sort: 'newest' });
    const duration = normalizeRunsQuery({ ...query, sort: 'duration' });
    const shortest = normalizeRunsQuery({ ...query, sort: 'shortest' });

    expect(newest.sort).toBe('newest');
    expect(duration.sort).toBe('duration');
    expect(shortest.sort).toBe('shortest');
    expect(runsQueryKey(duration)).not.toBe(runsQueryKey(newest));
    expect(runsQueryKey(shortest)).not.toBe(runsQueryKey(duration));
  });

  it('round-trips a URL-selected run without accepting an unknown kind', () => {
    expect(runSelectionParam('workflow', 'run:42')).toBe('workflow:run:42');
    expect(normalizeRunSelection('workflow:run:42')).toEqual({ kind: 'workflow', runId: 'run:42' });
    expect(normalizeRunSelection('unknown:run:42')).toBeNull();
  });
});

describe('run filter URL values', () => {
  it('preserves an explicit all-time period without reverting to the default week', () => {
    expect(normalizeRunPeriodParam('all')).toBe('');
    expect(normalizeRunPeriodParam(null)).toBe('week');
  });
});

describe('run time windows', () => {
  const now = Date.parse('2026-10-29T12:00:00Z');

  it.each([
    ['hour', 3_600_000],
    ['day', 86_400_000],
    ['week', 604_800_000],
    ['month', 2_592_000_000],
  ])('keeps the inclusive %s boundary and excludes older or undated runs', (period, duration) => {
    expect(runMatchesPeriod(row('boundary', new Date(now - duration).toISOString()), period, now)).toBe(true);
    expect(runMatchesPeriod(row('older', new Date(now - duration - 1).toISOString()), period, now)).toBe(false);
    expect(runMatchesPeriod(row('undated', null), period, now)).toBe(false);
  });

  it('compares UTC instants rather than lexically comparing timestamp offsets', () => {
    expect(runMatchesPeriod(row('boundary', '2026-10-29T07:00:00-04:00'), 'hour', now)).toBe(true);
    expect(runMatchesPeriod(row('older', '2026-10-29T12:59:59+02:00'), 'hour', now)).toBe(false);
  });

  it('reconciles concurrent hour/week snapshots against the correct live window', () => {
    vi.spyOn(Date, 'now').mockReturnValue(now);
    const hour = { ...query, period: 'hour' };
    const week = { ...query, period: 'week' };
    let state = pending(undefined, 'hour', hour);
    state = pending(state, 'week', week);
    const recent = { ...row('recent', '2026-10-29T11:30:00Z'), tokens: 20 };
    const older = { ...row('older', '2026-10-27T12:00:00Z'), tokens: 80 };
    for (const run of [recent, older]) state = reducer(state, applyUiEvent({ type: 'run.updated', payload: { run } }));
    for (const requestId of ['hour', 'week']) state = reducer(state, applyUiEvent({ type: 'runs.snapshot', payload: { request_id: requestId, runs: [], next_cursor: null } }));
    expect(state.runs.snapshots[runsQueryKey(hour)].runs.map((run) => run.run_id)).toEqual(['recent']);
    expect(state.runs.snapshots[runsQueryKey(week)].runs.map((run) => run.run_id)).toEqual(['recent', 'older']);
    expect(state.runs.snapshots[runsQueryKey(hour)].runs[0].tokens).toBe(20);
  });

  it('expires a live row from the hour window without dropping it from the week window', () => {
    const clock = vi.spyOn(Date, 'now').mockReturnValue(now);
    const hour = { ...query, period: 'hour' };
    const week = { ...query, period: 'week' };
    const run = row('aging', '2026-10-29T11:01:00Z');
    let state = pending(undefined, 'hour', hour);
    state = pending(state, 'week', week);
    for (const requestId of ['hour', 'week']) state = reducer(state, applyUiEvent({ type: 'runs.snapshot', payload: { request_id: requestId, runs: [run], next_cursor: null } }));
    clock.mockReturnValue(now + 120_000);
    state = reducer(state, applyUiEvent({ type: 'run.updated', payload: { run: { ...run, tokens: 99 } } }));
    expect(state.runs.snapshots[runsQueryKey(hour)].runs).toEqual([]);
    expect(state.runs.snapshots[runsQueryKey(week)].runs[0].tokens).toBe(99);
    expect(runMatchesPeriod(row('undated', null), '', now)).toBe(true);
  });
});

describe('incomplete terminal timing', () => {
  it('does not turn an interrupted run without a terminal event into a growing duration', () => {
    const run = { ...row('interrupted'), status: 'interrupted' };
    const now = Date.parse('2026-10-28T10:05:00Z');
    expect(runDurationMs(run, now)).toBeNull();
    expect(runDurationMs(run, now + 60_000)).toBeNull();
    expect(runDurationMs({ ...run, status: 'running' }, now)).toBe(300_000);
    expect(runDurationMs({ ...run, completed_at: '2026-10-28T10:01:00Z' }, now)).toBe(60_000);
  });

  it.each(['duration', 'shortest'])('places unknown terminal duration after measured runs for %s', (sort) => {
    const unknown = { ...row('unknown'), status: 'interrupted' };
    const measured = { ...row('measured'), status: 'completed', completed_at: '2026-10-28T10:01:00Z' };
    expect([unknown, measured].sort((left, right) => compareRunRows(left, right, sort)).map((run) => run.run_id)).toEqual(['measured', 'unknown']);
  });
});
