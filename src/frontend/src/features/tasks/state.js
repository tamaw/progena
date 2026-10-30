import { UI_EVENT } from '../../realtime/eventVocabulary';
/**
 * Tasks domain: the kanban board plus per-card brief content and activity logs,
 * kept live by `kanban.snapshot` / `task.content.updated` / `task.activity.updated`
 * events on /api/ui/stream.
 */
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle, addKeyedRequestLifecycle, completeKeyedRequest, selectKeyedRequest } from '../../store/async';

export const initialState = {
  tasks: {
    board: null,
    loaded: false,
    loading: false,
    error: null,
    requestId: null,
    contentById: {},
    activityById: {},
    activityRequestById: {},
  },
};

function cardIdOf(payload) {
  return typeof payload?.card_id === 'string' ? payload.card_id : null;
}

export const fetchKanbanBoard = createAcknowledgedThunk('uiData/fetchKanbanBoard', async ({ kanbanUrl }, { requestId }) => {
  return requestAcknowledged(kanbanUrl, { requestId });
});

export const fetchTaskActivity = createAcknowledgedThunk('uiData/fetchTaskActivity', async ({ taskActivityUrl, cardId }, { requestId }) => {
  return requestAcknowledged(
    `${taskActivityUrl}/${encodeURIComponent(cardId)}/activity`,
    { requestId },
  );
});

export const updateKanbanCard = createAcknowledgedThunk('uiData/updateKanbanCard', ({ kanbanUrl, cardId, payload }, { requestId }) => requestAcknowledged(
  `${kanbanUrl}/cards/${encodeURIComponent(cardId)}`,
  { method: 'PATCH', body: payload, requestId },
));

export const deleteKanbanCard = createAcknowledgedThunk('uiData/deleteKanbanCard', ({ kanbanUrl, cardId }, { requestId }) => requestAcknowledged(
  `${kanbanUrl}/cards/${encodeURIComponent(cardId)}`,
  { method: 'DELETE', requestId },
));

export const createTaskActivity = createAcknowledgedThunk('uiData/createTaskActivity', ({ tasksUrl, cardId, payload }, { requestId }) => requestAcknowledged(
  `${tasksUrl}/${encodeURIComponent(cardId)}/activity`,
  { method: 'POST', body: payload, requestId },
));

export const reducers = {
  kanbanSnapshotReceived(state, action) {
    state.tasks.board = action.payload || null;
    state.tasks.loaded = true;
    state.tasks.loading = false;
    state.tasks.error = null;
  },
  taskContentReceived(state, action) {
    const cardId = cardIdOf(action.payload);
    if (cardId) {
      state.tasks.contentById[cardId] = action.payload;
    }
  },
  taskActivityReceived(state, action) {
    const cardId = cardIdOf(action.payload);
    if (cardId) {
      state.tasks.activityById[cardId] = action.payload;
    }
  },
};

export const eventHandlers = {
  [UI_EVENT.KANBAN_SNAPSHOT]: (state, payload) => {
    state.tasks.board = payload || null;
    state.tasks.loaded = true;
    if (!state.tasks.loading || payload.request_id === state.tasks.requestId) {
      state.tasks.loading = false;
      state.tasks.error = null;
      state.tasks.requestId = null;
    }
  },
  [UI_EVENT.TASK_CONTENT_UPDATED]: (state, payload) => {
    const cardId = cardIdOf(payload);
    if (cardId) {
      state.tasks.contentById[cardId] = payload;
    }
  },
  [UI_EVENT.TASK_ACTIVITY_UPDATED]: (state, payload) => {
    const cardId = cardIdOf(payload);
    if (cardId) {
      state.tasks.activityById[cardId] = payload;
      completeKeyedRequest(state.tasks.activityRequestById, cardId, payload.request_id);
    }
  },
};

export function buildExtraReducers(builder) {
  addRequestLifecycle(builder, fetchKanbanBoard, {
    select: (state) => state.tasks,
    errorMessage: 'Failed to load tasks',
  });
  return addKeyedRequestLifecycle(builder, fetchTaskActivity, {
    selectRequests: (state) => state.tasks.activityRequestById,
    keyFromArgs: ({ cardId } = {}) => cardId,
    errorMessage: 'Failed to load task activity',
  });
}

export const selectKanbanBoard = (state) => state.uiData.tasks.board;
export const selectTasksState = (state) => state.uiData.tasks;
export const selectTaskContentEntry = (state, cardId) => (
  cardId ? state.uiData.tasks.contentById[cardId] || null : null
);
export const selectTaskActivityEntry = (state, cardId) => (
  cardId ? state.uiData.tasks.activityById[cardId] || null : null
);
export const selectTaskActivityRequest = (state, cardId) => (
  selectKeyedRequest(state.uiData.tasks.activityRequestById, cardId)
);
