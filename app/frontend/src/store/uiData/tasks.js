/**
 * Tasks domain: the kanban board plus per-card brief content and activity logs,
 * kept live by `kanban.snapshot` / `task.content.updated` / `task.activity.updated`
 * events on /api/ui/stream.
 */
import { createAsyncThunk } from '@reduxjs/toolkit';
import { requestJson } from '../http';

export const initialState = {
  tasks: {
    board: null,
    loaded: false,
    loading: false,
    error: null,
    contentById: {},
    activityById: {},
  },
};

function cardIdOf(payload) {
  return typeof payload?.card_id === 'string' ? payload.card_id : null;
}

export const fetchKanbanBoard = createAsyncThunk(
  'uiData/fetchKanbanBoard',
  ({ kanbanUrl }) => requestJson(kanbanUrl, { errorLabel: 'Failed to load tasks' }),
);

export const fetchTaskContent = createAsyncThunk(
  'uiData/fetchTaskContent',
  ({ taskContentUrl, cardId }) => requestJson(
    `${taskContentUrl}/${encodeURIComponent(cardId)}/content`,
    { errorLabel: 'Failed to load task brief' },
  ),
);

export const fetchTaskActivity = createAsyncThunk(
  'uiData/fetchTaskActivity',
  ({ taskActivityUrl, cardId }) => requestJson(
    `${taskActivityUrl}/${encodeURIComponent(cardId)}/activity`,
    { errorLabel: 'Failed to load task activity' },
  ),
);

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
  'kanban.snapshot': (state, payload) => {
    state.tasks.board = payload || null;
    state.tasks.loaded = true;
    state.tasks.loading = false;
    state.tasks.error = null;
  },
  'task.content.updated': (state, payload) => {
    const cardId = cardIdOf(payload);
    if (cardId) {
      state.tasks.contentById[cardId] = payload;
    }
  },
  'task.activity.updated': (state, payload) => {
    const cardId = cardIdOf(payload);
    if (cardId) {
      state.tasks.activityById[cardId] = payload;
    }
  },
};

export function buildExtraReducers(builder) {
  builder
    .addCase(fetchKanbanBoard.pending, (state) => {
      state.tasks.loading = true;
      state.tasks.error = null;
    })
    .addCase(fetchKanbanBoard.fulfilled, (state, action) => {
      state.tasks.board = action.payload || null;
      state.tasks.loading = false;
      state.tasks.loaded = true;
      state.tasks.error = null;
    })
    .addCase(fetchKanbanBoard.rejected, (state, action) => {
      state.tasks.loading = false;
      state.tasks.error = action.error?.message || 'Failed to load tasks';
    })
    .addCase(fetchTaskContent.fulfilled, (state, action) => {
      const cardId = cardIdOf(action.payload);
      if (cardId) {
        state.tasks.contentById[cardId] = action.payload;
      }
    })
    .addCase(fetchTaskActivity.fulfilled, (state, action) => {
      const cardId = cardIdOf(action.payload);
      if (cardId) {
        state.tasks.activityById[cardId] = action.payload;
      }
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
