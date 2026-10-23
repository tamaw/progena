/**
 * Model profiles domain: editable LLM model configurations kept live by
 * `profiles.snapshot` events on /api/ui/stream. RealtimeBridge hydrates the
 * catalogue on connection.ready and reconnect; edits complete only after a
 * correlated snapshot applies canonical state.
 */
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle } from '../../../store/async';

export const initialState = {
  modelProfiles: {
    profiles: [],
    loaded: false,
    loading: false,
    error: null,
    requestId: null,
    snapshotRequestId: null,
    saveRequest: {
      loading: false,
      error: null,
      requestId: null,
      completedRequestId: null,
    },
  },
};

export const fetchModelProfiles = createAcknowledgedThunk(
  'uiData/fetchModelProfiles',
  ({ modelProfilesUrl }, { requestId }) => requestAcknowledged(modelProfilesUrl, { requestId }),
);

export const patchModelProfile = createAcknowledgedThunk(
  'uiData/patchModelProfile',
  ({ modelProfilesUrl, name, patch }, { requestId }) => requestAcknowledged(
    `${modelProfilesUrl}/${encodeURIComponent(name)}`,
    { method: 'PATCH', body: patch, requestId },
  ),
);

export const deleteModelProfile = createAcknowledgedThunk(
  'uiData/deleteModelProfile',
  ({ modelProfilesUrl, name }, { requestId }) => requestAcknowledged(
    `${modelProfilesUrl}/${encodeURIComponent(name)}`,
    { method: 'DELETE', requestId },
  ),
);

export const eventHandlers = {
  'profiles.snapshot': (state, payload) => {
    state.modelProfiles.profiles = Array.isArray(payload.profiles) ? payload.profiles : [];
    state.modelProfiles.loaded = true;
    state.modelProfiles.snapshotRequestId = payload.request_id || null;
    if (!state.modelProfiles.loading || payload.request_id === state.modelProfiles.requestId) {
      state.modelProfiles.loading = false;
      state.modelProfiles.error = null;
      state.modelProfiles.requestId = null;
    }
    // Correlated save: clear saveRequest when the snapshot carries our save request_id.
    if (payload.request_id && payload.request_id === state.modelProfiles.saveRequest.requestId) {
      state.modelProfiles.saveRequest.loading = false;
      state.modelProfiles.saveRequest.error = null;
      state.modelProfiles.saveRequest.requestId = null;
      state.modelProfiles.saveRequest.completedRequestId = payload.request_id;
    }
  },
};

export function buildExtraReducers(builder) {
  addRequestLifecycle(builder, fetchModelProfiles, {
    select: (state) => state.modelProfiles,
    errorMessage: 'Failed to load model profiles',
  });
  return addRequestLifecycle(builder, patchModelProfile, {
    select: (state) => state.modelProfiles.saveRequest,
    errorMessage: 'Failed to save model profile',
    onPending: (state) => { state.modelProfiles.saveRequest.completedRequestId = null; },
  });
}

export const selectModelProfiles = (state) => state.uiData.modelProfiles.profiles;
export const selectModelProfilesState = (state) => state.uiData.modelProfiles;
