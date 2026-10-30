import { UI_EVENT } from '../../realtime/eventVocabulary';
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
  [UI_EVENT.CONNECTION_READY]: (state, payload) => {
    const widgets = widgetsFrom(payload.status) || widgetsFrom(payload);
    if (widgets) {
      state.widgets = widgets;
    }
  },
  [UI_EVENT.STATUS_UPDATED]: (state, payload) => {
    const widgets = widgetsFrom(payload);
    if (widgets) {
      state.widgets = widgets;
    }
  },
  [UI_EVENT.WIDGETS_UPDATED]: (state, payload) => {
    state.widgets = widgetsFrom(payload) || [];
  }
};
