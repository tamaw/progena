import { afterEach, describe, expect, it, vi } from 'vitest';
import reducer from '@frontend/store/uiDataSlice';
import { applyUiEvent } from '@frontend/store/uiEvents';
import { fetchRuns, runMatchesPeriod, runsQueryKey } from '@frontend/features/runs/state';

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
    expect(snapshot.runs.map((item) => item.run_id)).toEqual(['new']);
    expect(snapshot.stale).toBe(true);
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
