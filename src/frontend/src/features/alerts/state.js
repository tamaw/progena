import { createAcknowledgedThunk, requestAcknowledged } from '../../store/async';
import { arrayPayload } from '../../lib/eventPayload';

export const alertsFrom = (payload) => arrayPayload(payload, ['alerts']);

export function upsertAlert(alerts, alert) {
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

export const selectUiAlerts = (state) => state.uiData.alerts;

export const selectLastReceivedAlert = (state) => state.uiData.lastReceivedAlert;

export const initialState = {
  alerts: null,
  // Bumped (with a monotonic `seq`) on every single-new-`alert` event, for a
  // global toast+sound side effect (see AppRuntimeShell) - distinct from
  // `alerts`/`alerts.updated`, which is the durable list, not "just arrived".
  lastReceivedAlert: null
};

export const eventHandlers = {
  'connection.ready': (state, payload) => {
    const alerts = alertsFrom(payload.alerts) || alertsFrom(payload.status?.alerts);
    if (alerts) {
      state.alerts = alerts;
    }
  },
  'status.updated': (state, payload) => {
    const alerts = alertsFrom(payload.alerts);
    if (alerts) {
      state.alerts = alerts;
    }
  },
  'alerts.updated': (state, payload) => {
    state.alerts = alertsFrom(payload) || [];
  },
  alert: (state, payload) => {
    const alert = payload.alert || payload;
    state.alerts = upsertAlert([...(state.alerts || [])], alert);
    state.lastReceivedAlert = {
      ...alert,
      seq: (state.lastReceivedAlert?.seq || 0) + 1
    };
  }
};
