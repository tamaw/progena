import { describe, expect, it } from 'vitest';
import reducer, { applyUiEvent } from '../uiDataSlice';

function populated() {
  let state = reducer(undefined, { type: 'init' });
  state = reducer(state, applyUiEvent({ type: 'agents.snapshot', payload: { agents: [{ id: 'one' }, { id: 'two' }] } }));
  state = reducer(state, applyUiEvent({ type: 'agent.selected', payload: { agent: { id: 'one' } } }));
  state = reducer(state, applyUiEvent({ type: 'agent.routine.revisions.loaded', payload: { agent_id: 'one', revisions: [{ revision_id: 'old' }] } }));
  return state;
}

describe('agent deletion notifications', () => {
  it('removes the live roster/detail, preserves history and rejects a stale selection', () => {
    let state = populated();
    state = reducer(state, applyUiEvent({ type: 'agent.deleted', payload: { agent_id: 'one' } }));
    expect(state.agents.order).toEqual(['two']);
    expect(state.agents.byId.one).toBeUndefined();
    expect(state.agents.active.detail).toBeNull();
    expect(state.agents.revisionsByAgentId.one).toEqual([{ revision_id: 'old' }]);
    state = reducer(state, applyUiEvent({ type: 'agent.selected', payload: { agent: { id: 'one' } } }));
    expect(state.agents.active.detail).toBeNull();
    state = reducer(state, applyUiEvent({ type: 'agent.deleted', payload: { agent_id: 'one' } }));
    expect(state.agents.order).toEqual(['two']);
  });

  it('clears missing active details on a reconnect snapshot and permits recreation', () => {
    let state = populated();
    state = reducer(state, applyUiEvent({ type: 'agents.snapshot', payload: { agents: [{ id: 'two' }] } }));
    expect(state.agents.active.agentId).toBeNull();
    state = reducer(state, applyUiEvent({ type: 'agent.upserted', payload: { agent: { id: 'one' } } }));
    state = reducer(state, applyUiEvent({ type: 'agent.selected', payload: { agent: { id: 'one' } } }));
    expect(state.agents.active.agentId).toBe('one');
  });
});
