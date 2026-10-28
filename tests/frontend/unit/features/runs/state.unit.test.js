import { describe, expect, it } from 'vitest';
import reducer from '@frontend/store/uiDataSlice';
import { applyUiEvent } from '@frontend/store/uiEvents';
import { fetchRuns, runsQueryKey } from '@frontend/features/runs/state';

const query = { runsUrl: '/api/ui/runs', kind: '', status: '', q: '', cursor: null };
const row = (runId, startedAt = '2026-10-28T10:00:00Z') => ({
  kind: 'workflow', run_id: runId, name: runId, trigger: 'manual', status: 'running',
  started_at: startedAt, completed_at: null, steps_done: 1, steps_total: 3,
});

function pending(state, requestId, requestQuery = query) {
  return reducer(state, fetchRuns.pending(requestId, requestQuery));
}

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
    const completed = { ...row('target'), status: 'completed', steps_done: 3, completed_at: '2026-10-28T10:01:00Z' };
    state = reducer(state, applyUiEvent({ type: 'run.updated', payload: { run: completed } }));
    state = reducer(state, applyUiEvent({ type: 'runs.snapshot', payload: { request_id: 'all', runs: [row('target')], next_cursor: null } }));
    state = reducer(state, applyUiEvent({ type: 'runs.snapshot', payload: { request_id: 'search', runs: [row('target')], next_cursor: null } }));
    for (const activeQuery of [query, searchQuery]) {
      expect(state.runs.snapshots[runsQueryKey(activeQuery)].runs[0]).toMatchObject({
        run_id: 'target', status: 'completed', steps_done: 3, completed_at: completed.completed_at,
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
