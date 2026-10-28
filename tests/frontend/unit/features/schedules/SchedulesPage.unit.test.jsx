// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import realtimeReducer, { realtimeConnected } from '@frontend/realtime/realtimeSlice';
import uiDataReducer from '@frontend/store/uiDataSlice';
import { applyUiEvent } from '@frontend/store/uiEvents';
import editorDraftsReducer from '@frontend/store/editorDraftsSlice';
import SchedulesPage from '@frontend/features/schedules/SchedulesPage';

// Stable like the real providers: page effects depend on these callbacks.
vi.mock('@frontend/context/ConfigContext', () => {
  const config = { getApiUrl: (key) => `/api/${key}` };
  return { useConfig: () => config };
});
vi.mock('@frontend/context/NotificationContext', () => {
  const notifications = { show: () => {} };
  return { useNotifications: () => notifications };
});
vi.mock('@frontend/features/schedules/useSchedulesApi', () => {
  const api = { retriggerSchedule: () => Promise.resolve() };
  return { useSchedulesApi: () => api };
});
vi.mock('@frontend/features/schedules/useWidgetsApi', () => {
  const api = { updateWidget: () => Promise.resolve(), deleteWidget: () => Promise.resolve() };
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

it.each([
  ['Europe/London', 'America/New_York', 'Europe/London'],
  [undefined, 'America/New_York', 'America/New_York'],
  [undefined, '', 'UTC'],
])('renders schedule wall-clock time using entry %s before preference %s',
async (entryTimezone, preferenceTimezone, expectedZone) => {
  const store = configureStore({
    reducer: { realtime: realtimeReducer, uiData: uiDataReducer, editorDrafts: editorDraftsReducer },
  });
  store.dispatch(applyUiEvent({
    type: 'preferences.snapshot', payload: { timezone: preferenceTimezone },
  }));
  store.dispatch(applyUiEvent({
    type: 'schedule.snapshot',
    payload: { items: [{
      id: 'legacy', name: 'Legacy', timezone: entryTimezone,
      scheduled_at: '2026-07-15T13:00:00Z', next_run: '2026-07-15T13:00:00Z',
    }] },
  }));
  await act(async () => root.render(
    <Provider store={store}>
      <MemoryRouter initialEntries={['/schedules/legacy']}>
        <Routes>
          <Route path="/schedules/:scheduleId?/:titleSlug?" element={<SchedulesPage />} />
        </Routes>
      </MemoryRouter>
    </Provider>,
  ));
  const wallClock = new Date('2026-07-15T13:00:00Z').toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    timeZone: expectedZone, timeZoneName: 'short',
  });
  expect(host.textContent).toContain(`Once · ${wallClock}`);
  expect(host.textContent).toContain(expectedZone);
});

it('retains independent unsaved intent drafts for schedules A and B across selection changes', async () => {
  const store = configureStore({
    reducer: { realtime: realtimeReducer, uiData: uiDataReducer, editorDrafts: editorDraftsReducer },
  });
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
  const editIntent = () => Array.from(intentSection().querySelectorAll('button'))
    .find((button) => button.textContent.trim() === 'Edit').click();
  const enterIntent = async (value) => {
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(intentEditor(), value);
      intentEditor().dispatchEvent(new Event('input', { bubbles: true }));
    });
  };
  const selectSchedule = (name) => Array.from(host.querySelectorAll('.schedule-pane-row'))
    .find((row) => row.querySelector('.schedule-pane-row__name').textContent === name)
    .click();

  await act(async () => editIntent());
  await enterIntent('Alpha draft');
  await act(async () => selectSchedule('Beta'));
  expect(intentEditor()).toBeNull();

  await act(async () => editIntent());
  await enterIntent('Beta draft');
  await act(async () => selectSchedule('Alpha'));
  expect(intentEditor().value).toBe('Alpha draft');

  await act(async () => selectSchedule('Beta'));
  expect(intentEditor().value).toBe('Beta draft');
});

it('keeps a deep link that a cached list lacks until this visit’s refresh settles', async () => {
  let refreshRequestId = null;
  vi.stubGlobal('fetch', vi.fn(async (_url, { headers = {} } = {}) => {
    refreshRequestId = headers['X-Operation-Id'];
    return new Response(JSON.stringify({ request_id: refreshRequestId, event_types: ['schedule.snapshot'] }), { status: 202 });
  }));
  const store = configureStore({
    reducer: { realtime: realtimeReducer, uiData: uiDataReducer, editorDrafts: editorDraftsReducer },
  });
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
