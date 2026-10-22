import { combineReducers, configureStore } from '@reduxjs/toolkit';
import realtimeReducer from '../realtime/realtimeSlice';
import uiDataReducer from './uiDataSlice';
import editorDraftsReducer from './editorDraftsSlice';
import { realtimeListenerMiddleware } from '../realtime/realtimeListener';

export const WORKSPACE_SWITCHED = 'workspace/switched';
export const workspaceSwitched = () => ({ type: WORKSPACE_SWITCHED });

const appReducer = combineReducers({
  realtime: realtimeReducer,
  uiData: uiDataReducer,
  editorDrafts: editorDraftsReducer,
});

// Switching workspaces targets a different backend entirely -- every fetched
// slice (chats, agents, inbox, tasks, ...) and the realtime connection state
// belong to the *previous* backend and must not leak into the new one. Reset
// the whole tree to each reducer's initial state instead of hand-rolling a
// reset case per domain.
const rootReducer = (state, action) =>
  appReducer(action.type === WORKSPACE_SWITCHED ? undefined : state, action);

export const store = configureStore({
  reducer: rootReducer,
  middleware: (getDefaultMiddleware) =>
    getDefaultMiddleware().prepend(realtimeListenerMiddleware.middleware),
});

export default store;
