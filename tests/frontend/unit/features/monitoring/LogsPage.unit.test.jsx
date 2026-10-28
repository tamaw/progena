// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import realtimeReducer, { realtimeConnected } from '@frontend/realtime/realtimeSlice';
import uiDataReducer from '@frontend/store/uiDataSlice';
import LogsPage from '@frontend/features/monitoring/LogsPage';

const api = vi.hoisted(() => ({ getLogs: vi.fn() }));
vi.mock('@frontend/features/monitoring/useLogsApi', () => ({ useLogsApi: () => api }));
vi.mock('@frontend/context/ConfigContext', () => {
  const config = { getApiUrl: (path) => path };
  return { useConfig: () => config };
});
vi.mock('@frontend/context/NotificationContext', () => {
  const notifications = { show: () => {} };
  return { useNotifications: () => notifications };
});

let host;
let root;
function LocationProbe() {
  return <output data-testid="location">{useLocation().search}</output>;
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.stubGlobal('React', React);
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  api.getLogs.mockResolvedValue({ request_id: 'calendar-filter' });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function renderLogs(url) {
  const store = configureStore({ reducer: { realtime: realtimeReducer, uiData: uiDataReducer } });
  store.dispatch(realtimeConnected());
  await act(async () => root.render(
    <Provider store={store}>
      <MemoryRouter initialEntries={[url]}>
        <LogsPage />
        <LocationProbe />
      </MemoryRouter>
    </Provider>,
  ));
  return api.getLogs.mock.calls.at(-1)[0];
}

it.each([[2026, 10, 4], [2026, 4, 5], [2028, 2, 29], [2026, 12, 31]])(
  'queries the whole browser-local day %i-%i-%i, including DST boundaries',
  async (year, month, day) => {
    const selected = `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    const query = await renderLogs(`/logs?start=${selected}&end=${selected}`);
    const start = new Date(year, month - 1, day);
    const nextDay = new Date(year, month - 1, day + 1);
    expect(Date.parse(query.start)).toBe(start.getTime());
    expect(Date.parse(query.end)).toBe(nextDay.getTime() - 1);
    expect(query.start.endsWith('Z')).toBe(true);
    expect(query.end.endsWith('999999Z')).toBe(true);
    expect(host.querySelector('[data-testid="location"]').textContent).toContain(`start=${selected}`);
    expect(host.querySelector('[data-testid="location"]').textContent).not.toContain('T00');
  },
);

it('uses browser-local calendar dates for the initial seven-day range', async () => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-04T13:30:00Z'));
  const today = new Date();
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 6);
  const nextDay = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
  const query = await renderLogs('/logs');
  expect(Date.parse(query.start)).toBe(start.getTime());
  expect(Date.parse(query.end)).toBe(nextDay.getTime() - 1);
});
