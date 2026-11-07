import { describe, expect, it } from 'vitest';
import { UI_EVENT } from '@frontend/realtime/eventVocabulary';
import { eventHandlers, initialState } from '@frontend/features/workflows/state/workflows';

const run = (id, workflowId, minutesAgo) => {
  const stamp = new Date(Date.UTC(2026, 10, 7, 12, 0) - minutesAgo * 60_000).toISOString();
  return { run_id: id, workflow_id: workflowId, status: 'completed', queued_at: stamp, updated_at: stamp };
};
const freshState = () => structuredClone(initialState);
const snapshot = (state, payload) => eventHandlers[UI_EVENT.WORKFLOW_RUNS_SNAPSHOT](state, payload);

describe('workflow run history', () => {
  it('appends later pages without duplicating rows and keeps the server total', () => {
    const state = freshState();
    snapshot(state, { workflow_id: 'wf', offset: 0, total: 3, items: [run('r1', 'wf', 1), run('r2', 'wf', 2)] });
    snapshot(state, { workflow_id: 'wf', offset: 2, total: 3, items: [run('r2', 'wf', 2), run('r3', 'wf', 3)] });

    const history = state.workflows.runHistoryById.wf;
    expect(history.items.map((item) => item.run_id)).toEqual(['r1', 'r2', 'r3']);
    expect(history.total).toBe(3);
  });

  it('restarts from the top when offset 0 is requested again', () => {
    const state = freshState();
    snapshot(state, { workflow_id: 'wf', offset: 0, total: 2, items: [run('r1', 'wf', 1), run('r2', 'wf', 2)] });
    snapshot(state, { workflow_id: 'wf', offset: 0, total: 1, items: [run('r2', 'wf', 2)] });

    expect(state.workflows.runHistoryById.wf.items.map((item) => item.run_id)).toEqual(['r2']);
  });

  it('does not touch the recent-runs list', () => {
    const state = freshState();
    snapshot(state, { items: [run('r9', 'wf', 5)] });
    snapshot(state, { workflow_id: 'wf', offset: 0, total: 1, items: [run('r1', 'wf', 90)] });

    expect(state.workflows.runs.map((item) => item.run_id)).toEqual(['r9']);
  });

  it('adds a newer recent run to a loaded history but never an older pinned one', () => {
    const state = freshState();
    snapshot(state, { workflow_id: 'wf', offset: 0, total: 1, items: [run('r1', 'wf', 10)] });

    // A run paused for days is pinned into the recent list; it is already on a later page.
    snapshot(state, { items: [run('stale-open', 'wf', 60 * 24 * 3)] });
    expect(state.workflows.runHistoryById.wf.items.map((item) => item.run_id)).toEqual(['r1']);

    snapshot(state, { items: [run('new', 'wf', 1), run('r1', 'wf', 10)] });
    const history = state.workflows.runHistoryById.wf;
    expect(history.items.map((item) => item.run_id)).toEqual(['new', 'r1']);
    expect(history.total).toBe(2);
  });

  it('forgets a deleted workflow history', () => {
    const state = freshState();
    snapshot(state, { workflow_id: 'wf', offset: 0, total: 1, items: [run('r1', 'wf', 1)] });
    eventHandlers[UI_EVENT.WORKFLOW_DELETED](state, { workflow_id: 'wf' });

    expect(state.workflows.runHistoryById.wf).toBeUndefined();
  });
});
