import { createSelector } from '@reduxjs/toolkit';
import { UI_EVENT } from '../../../realtime/eventVocabulary';

export const initialState = {
  chatBackgroundTasks: {
    byThread: {},
  },
};

function applyTasks(state, payload) {
  const threadId = typeof payload?.thread_id === 'string' ? payload.thread_id : '';
  const revision = Number.isInteger(payload?.revision) ? payload.revision : 0;
  if (!threadId || !Array.isArray(payload?.tasks)) return;
  const current = state.chatBackgroundTasks.byThread[threadId];
  if (current && revision < current.revision) return;
  state.chatBackgroundTasks.byThread[threadId] = { revision, tasks: payload.tasks };
}

export const eventHandlers = {
  [UI_EVENT.CONNECTION_READY]: (state) => {
    // Revision counters are process-local. A restarted backend may begin at
    // zero; retain cached tasks until the reconnect snapshot replaces them.
    for (const snapshot of Object.values(state.chatBackgroundTasks.byThread)) snapshot.revision = -1;
  },
  [UI_EVENT.CHAT_BACKGROUND_TASKS_SNAPSHOT]: applyTasks,
  [UI_EVENT.CHAT_BACKGROUND_TASKS_UPDATED]: applyTasks,
};

const selectBackgroundTasksDomain = (state) => state.uiData.chatBackgroundTasks;
export const selectBackgroundTasks = createSelector(
  [selectBackgroundTasksDomain, (_state, threadId) => threadId],
  (domain, threadId) => (threadId ? domain.byThread[threadId]?.tasks || [] : []),
);
