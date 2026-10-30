import { UI_EVENT } from '../../../realtime/eventVocabulary';
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle } from '../../../store/async';
export const initialState = { policies: { items: [], loaded: false, loading: false, error: null } };
export const fetchPolicies = createAcknowledgedThunk('uiData/fetchPolicies', ({ url }, { requestId }) => requestAcknowledged(url, { requestId }));
export const savePolicy = createAcknowledgedThunk('uiData/savePolicy', ({ url, policy }, { requestId }) => requestAcknowledged(`${url}/${encodeURIComponent(policy.id)}`, { method: 'PUT', requestId, body: policy }));
export const eventHandlers = {
  [UI_EVENT.POLICIES_SNAPSHOT]: (state, payload) => { state.policies.items = payload.items || []; state.policies.loaded = true; state.policies.loading = false; state.policies.error = null; },
};
export const selectPolicies = (state) => state.uiData.policies;

export function buildExtraReducers(builder) {
  addRequestLifecycle(builder, fetchPolicies, { select: (state) => state.policies, errorMessage: 'Unable to load policies' });
}
