import { createAcknowledgedThunk, requestAcknowledged } from '../../store/async';
import { arrayPayload } from '../../lib/eventPayload';

export const widgetsFrom = (payload) => arrayPayload(payload, ['items', 'widgets']);

// Acknowledge a widget-list request; the snapshot arrives on the realtime
// stream as `widgets.updated` (the GET just triggers the broadcast).
export const fetchWidgets = createAcknowledgedThunk('uiData/fetchWidgets', async ({ widgetsUrl }, { requestId }) => {
  return requestAcknowledged(widgetsUrl, { requestId });
});

export const refreshWidget = createAcknowledgedThunk('uiData/refreshWidget', ({ widgetsUrl, widgetId }, { requestId }) => requestAcknowledged(
  `${widgetsUrl}/${encodeURIComponent(widgetId)}/refresh`,
  { method: 'POST', requestId },
));

export const updateWidget = createAcknowledgedThunk('uiData/updateWidget', ({ widgetsUrl, widgetId, updates }, { requestId }) => requestAcknowledged(
  `${widgetsUrl}/${encodeURIComponent(widgetId)}`,
  { method: 'PATCH', body: updates, requestId },
));

export const deleteWidget = createAcknowledgedThunk('uiData/deleteWidget', ({ widgetsUrl, widgetId }, { requestId }) => requestAcknowledged(
  `${widgetsUrl}/${encodeURIComponent(widgetId)}`,
  { method: 'DELETE', requestId },
));

export const selectUiWidgets = (state) => state.uiData.widgets;

export const initialState = {
  widgets: []
};

export const eventHandlers = {
  'connection.ready': (state, payload) => {
    const widgets = widgetsFrom(payload.status) || widgetsFrom(payload);
    if (widgets) {
      state.widgets = widgets;
    }
  },
  'status.updated': (state, payload) => {
    const widgets = widgetsFrom(payload);
    if (widgets) {
      state.widgets = widgets;
    }
  },
  'widgets.updated': (state, payload) => {
    state.widgets = widgetsFrom(payload) || [];
  }
};
