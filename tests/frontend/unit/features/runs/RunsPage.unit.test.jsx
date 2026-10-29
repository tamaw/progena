// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import realtimeReducer from '@frontend/realtime/realtimeSlice';
import uiDataReducer from '@frontend/store/uiDataSlice';

const runsHooks = vi.hoisted(() => ({
  query: { snapshot: null, loading: false, error: null, refresh: vi.fn(), streamReady: true },
  trace: { trace: null, loading: false, error: null, refresh: vi.fn(), streamReady: true },
}));

vi.mock('@frontend/features/runs/useRunsQuery', () => ({
  useRunsQuery: () => runsHooks.query,
  useRunTrace: () => runsHooks.trace,
}));
vi.mock('@frontend/context/ConfigContext', () => ({
  useConfig: () => ({ getApiUrl: (path) => `/api/ui/${path}` }),
}));
vi.mock('@frontend/realtime/realtimeEvents', () => ({
  subscribeUiEvent: () => () => {},
}));

import RunsPage from '@frontend/features/runs/RunsPage';

describe('RunsPage detached URL selection', () => {
  let host;
  let root;
  let refreshTrace;

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('React', React);
    vi.stubGlobal('requestAnimationFrame', (callback) => { callback(); return 1; });
    vi.stubGlobal('cancelAnimationFrame', () => {});
    refreshTrace = vi.fn();
    runsHooks.query = { snapshot: null, loading: false, error: null, refresh: vi.fn(), streamReady: true };
    runsHooks.trace = { trace: null, loading: true, error: null, refresh: refreshTrace, streamReady: true };
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
  });

  async function render() {
    const store = configureStore({ reducer: { realtime: realtimeReducer, uiData: uiDataReducer } });
    await act(async () => root.render(
      <Provider store={store}>
        <MemoryRouter initialEntries={['/runs?run=workflow:missing-run']}><RunsPage /></MemoryRouter>
      </Provider>,
    ));
  }

  it('keeps a URL-selected run visible while its off-page trace loads, fails, and retries', async () => {
    await render();
    expect(host.textContent).toContain('Loading selected run…');

    runsHooks.trace = { trace: null, loading: false, error: new Error('The server returned an unexpected response (404).'), refresh: refreshTrace, streamReady: true };
    await render();
    expect(host.querySelector('[role="alert"]')).not.toBeNull();
    await act(async () => host.querySelector('[role="alert"] button').click());
    expect(refreshTrace).toHaveBeenCalledOnce();
  });
});
