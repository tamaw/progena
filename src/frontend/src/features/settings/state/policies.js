import { UI_EVENT } from '../../../realtime/eventVocabulary';
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle } from '../../../store/async';
import { withQuery } from '../../../store/http';
import { realtimeClientId, waitForRealtimeClient } from '../../../realtime/realtimeClient';
export const initialState = { policies: { items: [], loaded: false, loading: false, error: null } };
async function requestForClient(url, options) {
  await waitForRealtimeClient();
  return requestAcknowledged(withQuery(url, { client_id: realtimeClientId }), options);
}
export const fetchPolicies = createAcknowledgedThunk('uiData/fetchPolicies', ({ url }, { requestId }) => requestForClient(url, { requestId }));
export const savePolicy = createAcknowledgedThunk('uiData/savePolicy', ({ url, policy }, { requestId }) => requestForClient(`${url}/${encodeURIComponent(policy.id)}`, { method: 'PUT', requestId, body: policy }));
export const eventHandlers = {
  [UI_EVENT.POLICIES_SNAPSHOT]: (state, payload) => { state.policies.items = payload.items || []; state.policies.loaded = true; state.policies.loading = false; state.policies.error = null; },
};
export const selectPolicies = (state) => state.uiData.policies;

export function buildExtraReducers(builder) {
  addRequestLifecycle(builder, fetchPolicies, { select: (state) => state.policies, errorMessage: 'Unable to load policies' });
}
