// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import realtimeReducer from '../store/realtimeSlice';
import uiDataReducer, { applyUiEvent } from '../store/uiDataSlice';
import SchedulesPage from './SchedulesPage';

vi.mock('../context/ConfigContext', () => ({ useConfig: () => ({ getApiUrl: (key) => `/api/${key}` }) }));
vi.mock('../context/NotificationContext', () => ({ useNotifications: () => ({ show: vi.fn() }) }));
vi.mock('../hooks/api/useSchedulesApi', () => ({ useSchedulesApi: () => ({ retriggerSchedule: vi.fn() }) }));
vi.mock('../hooks/api/useWidgetsApi', () => ({
  useWidgetsApi: () => ({ getWidgets: () => Promise.resolve(), updateWidget: vi.fn(), deleteWidget: vi.fn() }),
}));

let host;
let root;

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
