// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import realtimeReducer, { realtimeConnected } from '../store/realtimeSlice';
import uiDataReducer, { applyUiEvent } from '../store/uiDataSlice';
import PluginsPage from './PluginsPage';

vi.mock('../context/ConfigContext', () => ({ useConfig: () => ({ getApiUrl: (key) => `/api/${key}` }) }));

const NEW_CONVERSATION_NOTICE = 'Its skills will only be available starting in a new conversation.';

let host;
let root;
let installRequestId;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.stubGlobal('React', React);
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  installRequestId = null;
  // Every finite request is acknowledged; the plugin preview is the one plain JSON read.
  vi.stubGlobal('fetch', vi.fn(async (url, { method = 'GET', headers = {} } = {}) => {
    if (String(url).endsWith('/preview')) {
      return new Response(JSON.stringify({ skills: [] }), { status: 200 });
    }
    const requestId = headers['X-Request-ID'];
    if (method === 'POST') installRequestId = requestId;
    return new Response(JSON.stringify({ request_id: requestId, event_types: ['plugins.snapshot'] }), { status: 202 });
  }));
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

it('keeps a finished install reported after a reconnect snapshot replaces its request ID', async () => {
  const store = configureStore({ reducer: { realtime: realtimeReducer, uiData: uiDataReducer } });
  store.dispatch(realtimeConnected());
  store.dispatch(applyUiEvent({ type: 'marketplaces.snapshot', payload: { marketplaces: { market: {} } } }));
  store.dispatch(applyUiEvent({ type: 'plugins.snapshot', payload: { plugins: {} } }));
  store.dispatch(applyUiEvent({
    type: 'plugin.catalog.snapshot',
    payload: { name: 'market', catalog: { plugins: [{ name: 'helper', displayName: 'Helper' }] } },
  }));
  await act(async () => root.render(
    <Provider store={store}>
      <MemoryRouter initialEntries={['/plugins']}>
        <Routes>
          <Route path="/plugins/:marketplace?/:plugin?" element={<PluginsPage />} />
        </Routes>
      </MemoryRouter>
    </Provider>,
  ));
  await act(async () => {});

  await act(async () => host.querySelector('[aria-label="Install plugin"]').click());
  expect(installRequestId).toEqual(expect.any(String));
  const installed = { 'market/helper': { version: '1.0.0', skills: [] } };
  await act(async () => store.dispatch(applyUiEvent({
    type: 'plugins.snapshot', payload: { plugins: installed, request_id: installRequestId },
  })));
  expect(host.textContent).toContain(NEW_CONVERSATION_NOTICE);

  await act(async () => store.dispatch(applyUiEvent({
    type: 'plugins.snapshot', payload: { plugins: installed, request_id: 'reconnect-snapshot' },
  })));

  expect(host.textContent).toContain(NEW_CONVERSATION_NOTICE);
  expect(host.querySelector('.plugins-editor-footer').textContent).toBe('Plugin Helper installed.');
});
