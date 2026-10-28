import { describe, expect, it } from 'vitest';
import reducer from '@frontend/store/uiDataSlice';
import { applyUiEvent } from '@frontend/store/uiEvents';

const event = (status, requestId, timestamp, extra = {}) => applyUiEvent({
  type: `agent.routine.run.${status}`, seq: 1,
  payload: { agent_id: 'routine', session_id: 'shared', request_id: requestId, status, timestamp, ...extra },
});

describe('routine lifecycle', () => {

  it('lets a derived interrupted attempt settle as stopped or cancelled, but not fall back to started', () => {
    let state = reducer(undefined, event('interrupted', 'orphan', '2026-10-07T00:00:01Z', { next: ['fetch'] }));
    state = reducer(state, event('started', 'orphan', '2026-10-07T00:00:01Z'));
    expect(state.agents.runLifecycleByRequestId.orphan.status).toBe('interrupted');
    expect(state.agents.runLifecycleByRequestId.orphan.next).toEqual(['fetch']);
    state = reducer(state, event('stopped', 'orphan', '2026-10-07T00:05:00Z'));
    expect(state.agents.runLifecycleByRequestId.orphan.status).toBe('stopped');

    let cancelled = reducer(undefined, event('interrupted', 'other', '2026-10-07T00:00:01Z'));
    cancelled = reducer(cancelled, event('cancelled', 'other', '2026-10-07T00:05:00Z'));
    expect(cancelled.agents.runLifecycleByRequestId.other.status).toBe('cancelled');
  });
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

  it('keeps a control acknowledgement pending until a terminal execution event and rejects contradictory late terminals', () => {
    let state = reducer(undefined, event('started', 'attempt', '2026-10-07T00:00:01Z', {
      thread_id: 'routine:routine:revision:shared',
    }));
    state = reducer(state, applyUiEvent({
      type: 'agent.routine.control.updated',
      payload: {
        request_id: 'stop-operation',
        run: {
          agent_id: 'routine',
          session_id: 'shared',
          thread_id: 'routine:routine:revision:shared',
          request_id: 'attempt',
          status: 'stopping',
          timestamp: '2026-10-07T00:00:02Z',
        },
      },
    }));
    expect(state.agents.runLifecycleByRequestId.attempt.status).toBe('stopping');
    expect(state.agents.runLifecycleByRequestId['stop-operation']).toBeUndefined();

    state = reducer(state, event('completed', 'attempt', '2026-10-07T00:00:03Z', {
      thread_id: 'routine:routine:revision:shared',
    }));
    expect(state.agents.runLifecycleByRequestId.attempt.status).toBe('completed');

    state = reducer(state, applyUiEvent({
      type: 'agent.routine.control.updated',
      payload: {
        request_id: 'cancel-operation',
        run: {
          agent_id: 'routine',
          session_id: 'shared',
          thread_id: 'routine:routine:revision:shared',
          request_id: 'attempt',
          status: 'cancelled',
          timestamp: '2026-10-07T00:00:04Z',
        },
      },
    }));
    expect(state.agents.runLifecycleByRequestId.attempt.status).toBe('completed');
  });

  it('retains stopped as the terminal state when a late completion races the stop event', () => {
    let state = reducer(undefined, event('stopped', 'attempt', '2026-10-07T00:00:02Z'));
    state = reducer(state, event('completed', 'attempt', '2026-10-07T00:00:03Z'));
    expect(state.agents.runLifecycleByRequestId.attempt.status).toBe('stopped');
  });
});
