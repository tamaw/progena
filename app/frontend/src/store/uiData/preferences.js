/**
 * Preferences domain: the backend-persisted user profile (display name,
 * timezone), kept live by `preferences.snapshot` events on /api/ui/stream and
 * seeded via REST when the Settings panel mounts.
 */
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle } from './async';

export const initialState = {
  preferences: {
    displayName: '',
    timezone: '',
    loaded: false,
    loading: false,
    error: null,
    requestId: null,
  },
};

function applyPreferences(state, payload) {
  state.preferences.displayName = typeof payload.display_name === 'string' ? payload.display_name : '';
  state.preferences.timezone = typeof payload.timezone === 'string' ? payload.timezone : '';
  state.preferences.loaded = true;
  if (!state.preferences.loading || payload.request_id === state.preferences.requestId) {
    state.preferences.loading = false;
    state.preferences.error = null;
    state.preferences.requestId = null;
  }
}

// Acknowledge a read; the snapshot arrives on the realtime stream as
// `preferences.snapshot`.
export const fetchPreferences = createAcknowledgedThunk(
  'uiData/fetchPreferences',
  async ({ preferencesUrl }, { requestId }) => requestAcknowledged(preferencesUrl, { requestId }),
);

// Persist the full preferences document. The backend validates it, saves it,
// and broadcasts `preferences.snapshot` on the realtime stream.
export const savePreferences = createAcknowledgedThunk(
  'uiData/savePreferences',
  async ({ preferencesUrl, displayName, timezone }, { requestId }) =>
    requestAcknowledged(preferencesUrl, {
      method: 'PUT',
      body: { display_name: displayName, timezone },
      requestId,
    }),
);

export const eventHandlers = {
  'preferences.snapshot': (state, payload) => {
    applyPreferences(state, payload);
  },
};

export function buildExtraReducers(builder) {
  return addRequestLifecycle(builder, fetchPreferences, {
    select: (state) => state.preferences,
    errorMessage: 'Failed to load preferences',
  });
}

export const selectPreferences = (state) => state.uiData.preferences;
