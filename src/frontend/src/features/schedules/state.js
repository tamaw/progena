import { UI_EVENT } from '../../realtime/eventVocabulary';
import { createSelector } from '@reduxjs/toolkit';
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle } from '../../store/async';

function normalizeSchedule(schedule) {
  if (!schedule || typeof schedule !== 'object' || Array.isArray(schedule)) {
    return schedule;
  }
  const hasDestination = Object.prototype.hasOwnProperty.call(schedule, 'conversation_id');
  const nestedDestination = schedule.data && typeof schedule.data === 'object'
    ? schedule.data.conversation_id
    : undefined;
  if (!hasDestination && nestedDestination === undefined) {
    return schedule;
  }
  return {
    ...schedule,
    conversation_id: hasDestination ? schedule.conversation_id || null : nestedDestination || null,
  };
}

function schedulesFrom(payload) {
  return Array.isArray(payload.items) ? payload.items.map(normalizeSchedule) : [];
}

// Acknowledge a schedule-list request; the snapshot arrives on the realtime
// stream as `schedule.snapshot` (the GET just triggers the broadcast).
export const fetchSchedules = createAcknowledgedThunk('uiData/fetchSchedules', async ({ schedulesUrl }, { requestId }) => {
  return requestAcknowledged(schedulesUrl, { requestId });
});

export const createSchedule = createAcknowledgedThunk('uiData/createSchedule', ({ schedulesUrl, schedule }, { requestId }) => requestAcknowledged(
  schedulesUrl,
  { method: 'POST', body: schedule, requestId },
));

export const retriggerSchedule = createAcknowledgedThunk('uiData/retriggerSchedule', ({ schedulesUrl, scheduleId, text }, { requestId }) => requestAcknowledged(
  `${schedulesUrl}/${encodeURIComponent(scheduleId)}/trigger`,
  { method: 'POST', body: { text }, requestId },
));

export function buildExtraReducers(builder) {
  return addRequestLifecycle(builder, fetchSchedules, {
    select: (state) => state,
    loadingKey: 'scheduleLoading',
    errorKey: 'scheduleError',
    requestIdKey: 'scheduleRequestId',
    errorMessage: 'Failed to load schedules',
  });
}

export const selectSchedules = (state) => state.uiData.schedule;

export const selectSchedulesState = createSelector(
  [
    selectSchedules,
    (state) => state.uiData.scheduleLoaded,
    (state) => state.uiData.scheduleLoading,
    (state) => state.uiData.scheduleError,
    (state) => state.uiData.scheduleRequestId,
  ],
  (items, loaded, loading, error, requestId) => ({
    items, loaded, loading, error, requestId,
  }),
);

export const initialState = {
  // Schedule list, kept live by `schedule.snapshot` events on /api/ui/stream
  // and seeded once via REST when the Schedules page mounts.
  schedule: [],
  scheduleLoaded: false,
  scheduleLoading: false,
  scheduleError: null,
  scheduleRequestId: null
};

export const eventHandlers = {
  [UI_EVENT.SCHEDULE_SNAPSHOT]: (state, payload) => {
    state.schedule = schedulesFrom(payload);
    state.scheduleLoaded = true;
    if (!state.scheduleLoading || payload.request_id === state.scheduleRequestId) {
      state.scheduleLoading = false;
      state.scheduleError = null;
      state.scheduleRequestId = null;
    }
  }
};

export const reducers = {
  scheduleSnapshotReceived(state, action) {
    state.schedule = Array.isArray(action.payload) ? action.payload.map(normalizeSchedule) : [];
    state.scheduleLoaded = true;
    // This array action has no correlation ID, so it cannot complete a
    // pending request whose authoritative snapshot has not arrived yet.
    if (!state.scheduleLoading) state.scheduleError = null;
  }
};
