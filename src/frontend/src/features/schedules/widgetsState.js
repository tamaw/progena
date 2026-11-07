import { UI_EVENT } from '../../realtime/eventVocabulary';
import { createAcknowledgedThunk, requestAcknowledged } from '../../store/async';
import { withQuery } from '../../store/http';
import { arrayPayload } from '../../lib/eventPayload';
import { realtimeClientId, waitForRealtimeClient } from '../../realtime/realtimeClient';

export const widgetsFrom = (payload) => arrayPayload(payload, ['items', 'widgets']);

export const fetchWidgets = createAcknowledgedThunk('uiData/fetchWidgets', async ({ widgetsUrl }, { requestId }) => {
  await waitForRealtimeClient();
  return requestAcknowledged(withQuery(widgetsUrl, { client_id: realtimeClientId }), { requestId });
});

async function requestWidgetUpdate(url, options) {
  await waitForRealtimeClient();
  return requestAcknowledged(withQuery(url, { client_id: realtimeClientId }), options);
}

export const refreshWidget = createAcknowledgedThunk('uiData/refreshWidget', ({ widgetsUrl, widgetId }, { requestId }) => requestWidgetUpdate(
  `${widgetsUrl}/${encodeURIComponent(widgetId)}/refresh`,
  { method: 'POST', requestId },
));
export const updateWidget = createAcknowledgedThunk('uiData/updateWidget', ({ widgetsUrl, widgetId, updates }, { requestId }) => requestWidgetUpdate(
  `${widgetsUrl}/${encodeURIComponent(widgetId)}`,
  { method: 'PATCH', body: updates, requestId },
));
export const deleteWidget = createAcknowledgedThunk('uiData/deleteWidget', ({ widgetsUrl, widgetId }, { requestId }) => requestWidgetUpdate(
  `${widgetsUrl}/${encodeURIComponent(widgetId)}`,
  { method: 'DELETE', requestId },
));

export const selectUiWidgets = (state) => state.uiData.widgets;

export const initialState = {
  widgets: []
};

export const eventHandlers = {
  [UI_EVENT.WIDGETS_UPDATED]: (state, payload) => {
    state.widgets = widgetsFrom(payload) || [];
  }
};
