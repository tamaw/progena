import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle } from './async';

export const initialState = {
  routines: { items: [], detailById: {}, loading: false, error: null, request: null },
};

export const fetchRoutines = createAcknowledgedThunk('uiData/fetchRoutines', async ({ routinesUrl }, { requestId }) => (
  requestAcknowledged(routinesUrl, { requestId })
));

export const fetchRoutineDetail = createAcknowledgedThunk('uiData/fetchRoutineDetail', async ({ routinesUrl, routineId, revision }, { requestId }) => (
  requestAcknowledged(`${routinesUrl}/${encodeURIComponent(routineId)}${revision ? `?revision=${revision}` : ''}`, { requestId })
));

function upsert(state, routine) {
  const index = state.items.findIndex((item) => item.id === routine.id);
  if (index >= 0) state.items[index] = routine;
  else state.items.push(routine);
  state.items.sort((left, right) => String(right.updated_at).localeCompare(String(left.updated_at)));
}

export const eventHandlers = {
  'routines.snapshot': (state, payload) => {
    state.routines.items = Array.isArray(payload?.items) ? payload.items : [];
    state.routines.loading = false;
    state.routines.error = null;
  },
  'routine.updated': (state, payload) => {
    if (payload?.routine) upsert(state.routines, payload.routine);
  },
  'routine.detail': (state, payload) => {
    if (payload?.routine?.id) state.routines.detailById[payload.routine.id] = payload;
  },
};

export function buildExtraReducers(builder) {
  addRequestLifecycle(builder, fetchRoutines, {
    select: (state) => state.routines,
    errorMessage: 'Failed to load routines',
  });
  return addRequestLifecycle(builder, fetchRoutineDetail, {
    select: (state) => state.routines,
    errorMessage: 'Failed to load routine details',
  });
}

export const selectRoutines = (state) => state.uiData.routines.items;
export const selectRoutinesState = (state) => state.uiData.routines;
export const selectRoutineDetail = (state, routineId) => state.uiData.routines.detailById[routineId] || null;
