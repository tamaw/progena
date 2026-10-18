// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import realtimeReducer, { realtimeConnected } from '../store/realtimeSlice';
import uiDataReducer, { applyUiEvent } from '../store/uiDataSlice';
import SchedulesPage from './SchedulesPage';

// Stable like the real providers: page effects depend on these callbacks.
vi.mock('../context/ConfigContext', () => {
  const config = { getApiUrl: (key) => `/api/${key}` };
  return { useConfig: () => config };
});
vi.mock('../context/NotificationContext', () => {
  const notifications = { show: () => {} };
  return { useNotifications: () => notifications };
});
vi.mock('../hooks/api/useSchedulesApi', () => {
  const api = { retriggerSchedule: () => Promise.resolve() };
  return { useSchedulesApi: () => api };
});
vi.mock('../hooks/api/useWidgetsApi', () => {
  const api = { getWidgets: () => Promise.resolve(), updateWidget: () => Promise.resolve(), deleteWidget: () => Promise.resolve() };
  return { useWidgetsApi: () => api };
});

let host;
let root;

function LocationProbe() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.stubGlobal('React', React);
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

it('discards an unsaved intent draft when the user leaves and returns to a schedule', async () => {
  const store = configureStore({ reducer: { realtime: realtimeReducer, uiData: uiDataReducer } });
  store.dispatch(applyUiEvent({
    type: 'schedule.snapshot',
    payload: {
      items: [
        { id: 'a', name: 'Alpha', message: 'alpha intent', cron: '0 * * * *' },
        { id: 'b', name: 'Beta', message: 'beta intent', cron: '0 * * * *' },
      ],
    },
  }));
  await act(async () => root.render(
    <Provider store={store}>
      <MemoryRouter initialEntries={['/schedules/a']}>
        <Routes>
          <Route path="/schedules/:scheduleId?/:titleSlug?" element={<SchedulesPage />} />
        </Routes>
      </MemoryRouter>
    </Provider>,
  ));
  const intentSection = () => host.querySelector('.schedules-summary');
  const intentEditor = () => host.querySelector('textarea[aria-label="Schedule intent"]');
  const editButton = Array.from(intentSection().querySelectorAll('button'))
    .find((button) => button.textContent.trim() === 'Edit');
  const selectSchedule = (name) => Array.from(host.querySelectorAll('.schedule-pane-row'))
    .find((row) => row.querySelector('.schedule-pane-row__name').textContent === name)
    .click();

  await act(async () => editButton.click());
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(intentEditor(), 'Unsaved draft');
    intentEditor().dispatchEvent(new Event('input', { bubbles: true }));
  });
  await act(async () => selectSchedule('Beta'));
  await act(async () => selectSchedule('Alpha'));

  expect(intentEditor()).toBeNull();
  expect(intentSection().textContent).toContain('alpha intent');
});

it('keeps a deep link that a cached list lacks until this visit’s refresh settles', async () => {
  let refreshRequestId = null;
  vi.stubGlobal('fetch', vi.fn(async (_url, { headers = {} } = {}) => {
    refreshRequestId = headers['X-Request-ID'];
    return new Response(JSON.stringify({ request_id: refreshRequestId, event_types: ['schedule.snapshot'] }), { status: 202 });
  }));
  const store = configureStore({ reducer: { realtime: realtimeReducer, uiData: uiDataReducer } });
  store.dispatch(realtimeConnected());
  // Hydrated by an earlier visit, before schedule "b" was created elsewhere.
  store.dispatch(applyUiEvent({
    type: 'schedule.snapshot',
    payload: { items: [{ id: 'a', name: 'Alpha', message: 'alpha intent', cron: '0 * * * *' }] },
  }));
  await act(async () => root.render(
    <Provider store={store}>
      <MemoryRouter initialEntries={['/schedules/b']}>
        <LocationProbe />
        <Routes>
          <Route path="/schedules/:scheduleId?/:titleSlug?" element={<SchedulesPage />} />
        </Routes>
      </MemoryRouter>
    </Provider>,
  ));
  const location = () => host.querySelector('[data-testid="location"]').textContent;
  expect(location()).toBe('/schedules/b');

  await act(async () => store.dispatch(applyUiEvent({
    type: 'schedule.snapshot',
    payload: {
      request_id: refreshRequestId,
      items: [
        { id: 'a', name: 'Alpha', message: 'alpha intent', cron: '0 * * * *' },
        { id: 'b', name: 'Beta', message: 'beta intent', cron: '0 * * * *' },
      ],
    },
  })));
  expect(location()).toBe('/schedules/b/beta');
  expect(host.querySelector('.schedules-summary').textContent).toContain('beta intent');
});
