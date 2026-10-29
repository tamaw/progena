// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import realtimeReducer, { realtimeConnected, realtimeStreamReady } from '@frontend/realtime/realtimeSlice';
import uiDataReducer from '@frontend/store/uiDataSlice';
import { fetchRuns, runsQueryKey } from '@frontend/features/runs/state';
import { applyUiEvent } from '@frontend/store/uiEvents';
import { setRealtimeClientReady } from '@frontend/realtime/realtimeClient';
import { useRunsQuery } from '@frontend/features/runs/useRunsQuery';

function QueryProbe() {
  const { refresh } = useRunsQuery({
    runsUrl: '/api/ui/runs', kind: '', status: '', q: '', cursor: null, period: 'week', sort: 'newest',
  });
  return <button type="button" onClick={() => refresh()}>Retry hydration</button>;
}

async function settleRequest() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

const query = {
  runsUrl: '/api/ui/runs', kind: '', status: '', q: '', cursor: null, period: 'week', sort: 'newest',
};
const row = (runId) => ({
  kind: 'workflow', run_id: runId, name: runId, status: 'completed', started_at: '2026-10-29T00:00:00Z',
});

describe('useRunsQuery hydration retry', () => {
  let host;
  let root;
  let store;
  let resolveFetch;

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('React', React);
    vi.stubGlobal('fetch', vi.fn(() => new Promise((resolve) => { resolveFetch = resolve; })));
    setRealtimeClientReady(true);
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    store = configureStore({ reducer: { realtime: realtimeReducer, uiData: uiDataReducer } });
    store.dispatch(fetchRuns.pending('cached', query));
    store.dispatch(applyUiEvent({ type: 'runs.snapshot', payload: {
      request_id: 'cached', runs: [row('deleted-from-server')], next_cursor: null,
    } }));
    store.dispatch(realtimeConnected());
    store.dispatch(realtimeStreamReady());
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    setRealtimeClientReady(false);
    vi.unstubAllGlobals();
  });

  it('retries a failed initial hydration as an authoritative replacement', async () => {
    await act(async () => root.render(<Provider store={store}><QueryProbe /></Provider>));
    await act(async () => {
      resolveFetch({ status: 503, text: async () => '' });
      await settleRequest();
    });

    await act(async () => host.querySelector('button').click());
    const requestId = store.getState().uiData.runs.requests[runsQueryKey(query)].requestId;
    await act(async () => {
      store.dispatch(applyUiEvent({ type: 'runs.snapshot', payload: {
        request_id: requestId, runs: [row('authoritative')], next_cursor: null,
      } }));
      resolveFetch({
        status: 202,
        text: async () => JSON.stringify({ request_id: requestId, event_types: ['runs.snapshot'] }),
      });
      await settleRequest();
    });

    expect(store.getState().uiData.runs.snapshots[runsQueryKey(query)].runs.map((item) => item.run_id))
      .toEqual(['authoritative']);
  });
});
