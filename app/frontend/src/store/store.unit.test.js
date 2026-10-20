import { describe, it, expect } from 'vitest';
import { store, workspaceSwitched } from './store';
import { fetchAgentProfiles } from './uiData/agents';
import { realtimeConnected } from './realtimeSlice';

describe('workspaceSwitched', () => {

  it('resets every slice back to its initial state', () => {
    const initialUiData = store.getState().uiData;
    const initialRealtime = store.getState().realtime;

    store.dispatch(realtimeConnected());
    store.dispatch({
      type: fetchAgentProfiles.pending.type,
      meta: { requestId: 'req-1' },
    });

    expect(store.getState().realtime).not.toEqual(initialRealtime);
    expect(store.getState().uiData.agents.loading).toBe(true);

    store.dispatch(workspaceSwitched());

    expect(store.getState().uiData).toEqual(initialUiData);
    expect(store.getState().realtime).toEqual(initialRealtime);
  });

  it('leaves state untouched for unrelated actions', () => {
    const before = store.getState();
    store.dispatch({ type: 'some/unrelated-action' });
    expect(store.getState()).toBe(before);
  });
});
