import { UI_EVENT } from '../../realtime/eventVocabulary';
import { createAcknowledgedThunk, requestAcknowledged } from '../../store/async';
import { withQuery } from '../../store/http';
import { realtimeClientId, waitForRealtimeClient } from '../../realtime/realtimeClient';

export const selectUiStatus = (state) => state.uiData.status;

export const initialState = {
  status: null
};

// Runtime status is the Dashboard's alone: it asks for a full snapshot when the
// page opens (and after a reconnect) and then watches the `dashboard` topic.
export const fetchStatus = createAcknowledgedThunk('uiData/fetchStatus', async ({ statusUrl }, { requestId }) => {
  await waitForRealtimeClient();
  return requestAcknowledged(withQuery(statusUrl, { client_id: realtimeClientId }), { requestId });
});

export const eventHandlers = {
  [UI_EVENT.STATUS_UPDATED]: (state, payload) => {
    // Merge rather than replace so a partial push (e.g. the periodic
    // process-metrics tick) updates only the fields it carries and leaves the
    // rest (loops, backlog, ...) intact.
    state.status = {
      ...state.status,
      ...payload
    };
  }
};
