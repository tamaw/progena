/**
 * Preferences domain: the backend-persisted user profile (display name,
 * timezone, UI/UX settings: theme, nav-rail collapse, pinned panel,
 * notifications), kept live by `preferences.snapshot` events on
 * /api/ui/stream and seeded via REST on app shell mount.
 */
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle } from './async';

const VALID_PINNED_PANELS = new Set(['quickChat', 'alerts', 'insights']);

export const initialState = {
  preferences: {
    displayName: '',
    timezone: '',
    theme: 'system',
    navCollapsed: false,
    pinnedPanel: null,
    notificationsEnabled: true,
    notificationSounds: false,
    loaded: false,
    loading: false,
    error: null,
    requestId: null,
  },
};

function applyPreferences(state, payload) {
  state.preferences.displayName = typeof payload.display_name === 'string' ? payload.display_name : '';
  state.preferences.timezone = typeof payload.timezone === 'string' ? payload.timezone : '';
  state.preferences.theme = ['system', 'dark', 'light'].includes(payload.theme) ? payload.theme : 'system';
  state.preferences.navCollapsed = payload.nav_collapsed === true;
  state.preferences.pinnedPanel = VALID_PINNED_PANELS.has(payload.pinned_panel) ? payload.pinned_panel : null;
  state.preferences.notificationsEnabled = payload.notifications_enabled !== false;
  state.preferences.notificationSounds = payload.notification_sounds === true;
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
// and broadcasts `preferences.snapshot` on the realtime stream. This is a
// whole-document PUT, so every call must carry the complete current state
// (see `selectPreferencesSaveRequest`) — a partial payload would silently
// reset the fields it omits back to their schema defaults.
export const savePreferences = createAcknowledgedThunk(
  'uiData/savePreferences',
  async (
    {
      preferencesUrl,
      displayName,
      timezone,
      theme,
      navCollapsed,
      pinnedPanel,
      notificationsEnabled,
      notificationSounds,
    },
    { requestId },
  ) =>
    requestAcknowledged(preferencesUrl, {
      method: 'PUT',
      body: {
        display_name: displayName,
        timezone,
        theme,
        nav_collapsed: navCollapsed,
        pinned_panel: pinnedPanel,
        notifications_enabled: notificationsEnabled,
        notification_sounds: notificationSounds,
      },
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

// Build a `savePreferences` argument (minus `preferencesUrl`) that carries
// every current field, so a caller can override just the one it's changing:
// `dispatch(savePreferences({ preferencesUrl, ...selectPreferencesSaveRequest(state), theme: next }))`.
export const selectPreferencesSaveRequest = (state) => {
  const preferences = selectPreferences(state);
  return {
    displayName: preferences.displayName,
    timezone: preferences.timezone,
    theme: preferences.theme,
    navCollapsed: preferences.navCollapsed,
    pinnedPanel: preferences.pinnedPanel,
    notificationsEnabled: preferences.notificationsEnabled,
    notificationSounds: preferences.notificationSounds,
  };
};
