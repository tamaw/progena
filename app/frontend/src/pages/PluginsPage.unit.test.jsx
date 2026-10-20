// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import realtimeReducer, { realtimeConnected, uiEventReceived } from '../store/realtimeSlice';
import uiDataReducer, { applyUiEvent } from '../store/uiDataSlice';
import PluginsPage from './PluginsPage';

vi.mock('../context/ConfigContext', () => ({ useConfig: () => ({ getApiUrl: (key) => `/api/${key}` }) }));

const NEW_CONVERSATION_NOTICE = 'Its skills will only be available starting in a new conversation.';
const INSTALLED = { 'market/helper': { version: '1.0.0', skills: [] } };

let host;
let root;
let store;
let eventCount;
let installRequestId;
let beforeInstallAcknowledged;

// Mirror the realtime listener: data reducers and the per-request event record.
function deliver(type, payload) {
  eventCount += 1;
  const event = { id: `event-${eventCount}`, type, payload };
  store.dispatch(applyUiEvent(event));
  store.dispatch(uiEventReceived(event));
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.stubGlobal('React', React);
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  eventCount = 0;
  installRequestId = null;
  beforeInstallAcknowledged = () => {};
  // Every finite request is acknowledged; the plugin preview is the one plain JSON read.
  vi.stubGlobal('fetch', vi.fn(async (url, { method = 'GET', headers = {} } = {}) => {
    if (String(url).endsWith('/preview')) {
      return new Response(JSON.stringify({ skills: [] }), { status: 200 });
    }
    const requestId = headers['X-Request-ID'];
    if (method === 'POST') {
      installRequestId = requestId;
      beforeInstallAcknowledged(requestId);
    }
    return new Response(JSON.stringify({ request_id: requestId, event_types: ['plugins.snapshot'] }), { status: 202 });
  }));
  store = configureStore({ reducer: { realtime: realtimeReducer, uiData: uiDataReducer } });
  store.dispatch(realtimeConnected());
  deliver('marketplaces.snapshot', { marketplaces: { market: {} } });
  deliver('plugins.snapshot', { plugins: {} });
  deliver('plugin.catalog.snapshot', { name: 'market', catalog: { plugins: [{ name: 'helper', displayName: 'Helper' }] } });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

async function renderAndInstall() {
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
}

function expectInstallReported() {
  expect(host.textContent).toContain(NEW_CONVERSATION_NOTICE);
  expect(host.querySelector('.plugins-editor-footer').textContent).toBe('Plugin Helper installed.');
}

it('keeps a finished install reported after a reconnect snapshot replaces its request ID', async () => {
  await renderAndInstall();
  await act(async () => deliver('plugins.snapshot', { plugins: INSTALLED, request_id: installRequestId }));
  expectInstallReported();

  await act(async () => deliver('plugins.snapshot', { plugins: INSTALLED, request_id: 'reconnect-snapshot' }));
  expectInstallReported();
});

it('reports an install whose snapshot and a later snapshot both arrive before its acknowledgement', async () => {
  beforeInstallAcknowledged = (requestId) => {
    deliver('plugins.snapshot', { plugins: INSTALLED, request_id: requestId });
    deliver('plugins.snapshot', { plugins: INSTALLED, request_id: 'watcher-snapshot' });
  };
  await renderAndInstall();
  expectInstallReported();
});
