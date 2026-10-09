import { describe, expect, it } from 'vitest';
import reducer, { applyUiEvent } from './uiDataSlice';

const event = (status, requestId, timestamp, extra = {}) => applyUiEvent({
  type: `agent.routine.run.${status}`, seq: 1,
  payload: { agent_id: 'routine', session_id: 'shared', request_id: requestId, status, timestamp, ...extra },
});

describe('routine lifecycle', () => {
  it('retains independent attempts on the same resumed thread and ignores late status', () => {
    let state = reducer(undefined, event('paused', 'one', '2026-10-07T00:00:02Z'));
    state = reducer(state, event('started', 'two', '2026-10-07T00:00:03Z'));
    state = reducer(state, event('started', 'one', '2026-10-07T00:00:01Z'));
    expect(state.agents.runLifecycleByRequestId.one.status).toBe('paused');
    expect(state.agents.runLifecycleByRequestId.two.status).toBe('started');
  });

  it('hydrates recovery state without displaying old completion notifications', () => {
    const state = reducer(undefined, applyUiEvent({ type: 'agent.routine.lifecycle.snapshot', payload: {
      runs: [{ agent_id: 'routine', request_id: 'old', status: 'completed', timestamp: '2026-10-07T00:00:01Z' }],
    } }));
    expect(state.agents.runLifecycleByRequestId.old.status).toBe('completed');
    expect(state.agents.lastRunNotification).toBeNull();
  });

  it('records global success/error notifications once, while no routine detail is loaded', () => {
    let state = reducer(undefined, event('completed', 'one', '2026-10-07T00:00:01Z'));
    expect(state.agents.lastRunNotification.status).toBe('completed');
    const notification = state.agents.lastRunNotification;
    state = reducer(state, event('completed', 'one', '2026-10-07T00:00:01Z'));
    expect(state.agents.lastRunNotification).toBe(notification);
    state = reducer(state, event('failed', 'two', '2026-10-07T00:00:02Z', { error: 'bad input' }));
    expect(state.agents.lastRunNotification.error).toBe('bad input');
    expect(state.agents.runStateByThreadId).toEqual({});
  });
});
