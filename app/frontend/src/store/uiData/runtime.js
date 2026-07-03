/**
 * Runtime domain: process status, alert center, dashboard widgets, and the
 * schedule list. These are kept live by `status.updated` / `alerts.updated` /
 * `widgets.updated` / `schedule.snapshot` frames on the realtime stream.
 */
import { createAsyncThunk } from '@reduxjs/toolkit';
import { requestJson } from '../http';

export const initialState = {
  status: null,
  alerts: null,
  widgets: [],
  // Schedule list, kept live by `schedule.snapshot` events on /api/ui/stream
  // and seeded once via REST when the Schedules page mounts.
  schedule: [],
};

// Acknowledge a widget-list request; the snapshot arrives on the realtime
// stream as `widgets.updated` (the GET just triggers the broadcast).
export const fetchWidgets = createAsyncThunk(
  'uiData/fetchWidgets',
  ({ widgetsUrl }) => requestJson(widgetsUrl, { errorLabel: 'Failed to load widgets' }),
);

export const reducers = {
  scheduleSnapshotReceived(state, action) {
    state.schedule = Array.isArray(action.payload) ? action.payload : [];
  },
};

export const eventHandlers = {
  'connection.ready': (state, payload) => {
    state.status = payload.status || state.status;
    state.alerts = payload.alerts || state.alerts;
  },
  'status.updated': (state, payload) => {
    // Merge rather than replace so a partial push (e.g. the periodic
    // process-metrics tick) updates only the fields it carries and leaves the
    // rest (agents/tools/schedule/...) intact.
    state.status = { ...state.status, ...payload };
    if (payload.alerts) {
      state.alerts = payload.alerts;
    }
    if (Array.isArray(payload.widgets)) {
      state.widgets = payload.widgets;
    }
  },
  'alerts.updated': (state, payload) => {
    state.alerts = payload;
  },
  'widgets.updated': (state, payload) => {
    state.widgets = Array.isArray(payload.items) ? payload.items : [];
  },
  'schedule.snapshot': (state, payload) => {
    state.schedule = Array.isArray(payload.items) ? payload.items : [];
  },
};

export const selectUiStatus = (state) => state.uiData.status;
export const selectUiAlerts = (state) => state.uiData.alerts;
export const selectUiWidgets = (state) => state.uiData.widgets;
export const selectSchedules = (state) => state.uiData.schedule;
