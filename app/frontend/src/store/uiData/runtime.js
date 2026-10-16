/**
 * Runtime domain: process status, alert center, dashboard widgets, and the
 * schedule list. These are kept live by `status.updated` / `alerts.updated` /
 * `widgets.updated` / `schedule.snapshot` frames on the realtime stream.
 */
import { createSelector } from '@reduxjs/toolkit';
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle } from './async';

export const initialState = {
  status: null,
  alerts: null,
  widgets: [],
  // Schedule list, kept live by `schedule.snapshot` events on /api/ui/stream
  // and seeded once via REST when the Schedules page mounts.
  schedule: [],
  scheduleLoaded: false,
  scheduleLoading: false,
  scheduleError: null,
  scheduleRequestId: null,
  // Bumped (with a monotonic `seq`) on every single-new-`alert` event, for a
  // global toast+sound side effect (see AppRuntimeShell) - distinct from
  // `alerts`/`alerts.updated`, which is the durable list, not "just arrived".
  lastReceivedAlert: null,
};
function arrayPayload(payload, keys = []) {
  if (Array.isArray(payload)) {
    return payload;
  }
  for (const key of keys) {
    if (Array.isArray(payload?.[key])) {
      return payload[key];
    }
  }
  return null;
}

const alertsFrom = (payload) => arrayPayload(payload, ['alerts']);
const widgetsFrom = (payload) => arrayPayload(payload, ['items', 'widgets']);
function upsertAlert(alerts, alert) {
  if (!alert?.id) {
    return alerts;
  }
  const index = alerts.findIndex((item) => item?.id === alert.id);
  if (index >= 0) {
    alerts[index] = { ...alerts[index], ...alert };
  } else {
    alerts.unshift(alert);
  }
  return alerts;
}


// Acknowledge a widget-list request; the snapshot arrives on the realtime
// stream as `widgets.updated` (the GET just triggers the broadcast).
export const fetchWidgets = createAcknowledgedThunk('uiData/fetchWidgets', async ({ widgetsUrl }, { requestId }) => {
  return requestAcknowledged(widgetsUrl, { requestId });
});

// Acknowledge a schedule-list request; the snapshot arrives on the realtime
// stream as `schedule.snapshot` (the GET just triggers the broadcast).
export const fetchSchedules = createAcknowledgedThunk('uiData/fetchSchedules', async ({ schedulesUrl }, { requestId }) => {
  return requestAcknowledged(schedulesUrl, { requestId });
});

// Alert read acknowledgements deliberately carry no alert data. The correlated
// `alerts.updated` stream frame below is the sole owner of the alert list and
// its unread/viewed state.
export const markAlertsViewed = createAcknowledgedThunk('uiData/markAlertsViewed', ({ alertsUrl, alertIds }, { requestId }) => requestAcknowledged(
  `${alertsUrl.replace(/\/$/, '')}/viewed`,
  {
    method: 'POST',
    body: Array.isArray(alertIds) && alertIds.length > 0 ? { alert_ids: alertIds } : {},
    requestId,
  },
));

export const reducers = {
  scheduleSnapshotReceived(state, action) {
    state.schedule = Array.isArray(action.payload) ? action.payload : [];
    state.scheduleLoaded = true;
    // This array action has no correlation ID, so it cannot complete a
    // pending request whose authoritative snapshot has not arrived yet.
    if (!state.scheduleLoading) state.scheduleError = null;
  },
};

export const eventHandlers = {
  'connection.ready': (state, payload) => {
    if (payload.status) {
      state.status = payload.status;
    }
    const alerts = alertsFrom(payload.alerts) || alertsFrom(payload.status?.alerts);
    if (alerts) {
      state.alerts = alerts;
    }
    const widgets = widgetsFrom(payload.status) || widgetsFrom(payload);
    if (widgets) {
      state.widgets = widgets;
    }
  },
  'status.updated': (state, payload) => {
    // Merge rather than replace so a partial push (e.g. the periodic
    // process-metrics tick) updates only the fields it carries and leaves the
    // rest (agents/tools/schedule/...) intact.
    state.status = { ...state.status, ...payload };
    const alerts = alertsFrom(payload.alerts);
    if (alerts) {
      state.alerts = alerts;
    }
    const widgets = widgetsFrom(payload);
    if (widgets) {
      state.widgets = widgets;
    }
  },
  'alerts.updated': (state, payload) => {
    state.alerts = alertsFrom(payload) || [];
  },
  alert: (state, payload) => {
    const alert = payload.alert || payload;
    state.alerts = upsertAlert([...(state.alerts || [])], alert);
    state.lastReceivedAlert = { ...alert, seq: (state.lastReceivedAlert?.seq || 0) + 1 };
  },
  'widgets.updated': (state, payload) => {
    state.widgets = widgetsFrom(payload) || [];
  },
  'schedule.snapshot': (state, payload) => {
    state.schedule = Array.isArray(payload.items) ? payload.items : [];
    state.scheduleLoaded = true;
    if (!state.scheduleLoading || payload.request_id === state.scheduleRequestId) {
      state.scheduleLoading = false;
      state.scheduleError = null;
      state.scheduleRequestId = null;
    }
  },
};

export function buildExtraReducers(builder) {
  return addRequestLifecycle(builder, fetchSchedules, {
    select: (state) => state,
    loadingKey: 'scheduleLoading',
    errorKey: 'scheduleError',
    requestIdKey: 'scheduleRequestId',
    errorMessage: 'Failed to load schedules',
  });
}

export const selectUiStatus = (state) => state.uiData.status;
export const selectUiAlerts = (state) => state.uiData.alerts;
export const selectUiWidgets = (state) => state.uiData.widgets;
export const selectSchedules = (state) => state.uiData.schedule;
export const selectSchedulesState = createSelector(
  [
    selectSchedules,
    (state) => state.uiData.scheduleLoaded,
    (state) => state.uiData.scheduleLoading,
    (state) => state.uiData.scheduleError,
    (state) => state.uiData.scheduleRequestId,
  ],
  (items, loaded, loading, error, requestId) => ({
    items, loaded, loading, error, requestId,
  }),
);
export const selectLastReceivedAlert = (state) => state.uiData.lastReceivedAlert;
