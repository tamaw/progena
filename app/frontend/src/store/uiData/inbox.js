/**
 * Inbox domain: agent-to-user messages written as /inbox/<id>.md files in the
 * runtime, kept live by `inbox.snapshot` / `inbox.message.upserted` /
 * `inbox.message.deleted` events on /api/ui/stream. Snapshots carry full
 * markdown bodies (messages are small), so there is no detail request dance.
 */
import { createSelector } from '@reduxjs/toolkit';
import { createAcknowledgedThunk, requestAcknowledged } from './async';


export const initialState = {
  inbox: {
    byId: {},
    order: [],
    loaded: false,
    loading: false,
    error: null,
    requestId: null,
  },
};

function replaceMessageList(inbox, items) {
  inbox.byId = {};
  inbox.order = [];
  for (const message of Array.isArray(items) ? items : []) {
    const messageId = typeof message?.id === 'string' ? message.id : null;
    if (!messageId || inbox.byId[messageId]) {
      continue;
    }
    inbox.byId[messageId] = message;
    inbox.order.push(messageId);
  }
}

// Used by the inbox.snapshot event handler to fully replace the inbox state.
// No thunk fulfilled case sets domain data — the events drive hydration.
function applyInboxSnapshot(inbox, payload) {
  replaceMessageList(inbox, payload?.messages);
  inbox.loaded = true;
  if (!inbox.loading || payload?.request_id === inbox.requestId) {
    inbox.loading = false;
    inbox.error = null;
    inbox.requestId = null;
  }
}

export const fetchInbox = createAcknowledgedThunk('uiData/fetchInbox', ({ inboxUrl }, { requestId }) => requestAcknowledged(
  inboxUrl,
  { requestId },
));

export const markInboxMessageRead = createAcknowledgedThunk('uiData/markInboxMessageRead', ({ inboxUrl, messageId, read = true }, { requestId }) => requestAcknowledged(
  `${inboxUrl}/${encodeURIComponent(messageId)}/read`,
  { method: 'POST', body: { read }, requestId },
));
export const deleteInboxMessage = createAcknowledgedThunk('uiData/deleteInboxMessage', ({ inboxUrl, messageId }, { requestId }) => requestAcknowledged(
  `${inboxUrl}/${encodeURIComponent(messageId)}`,
  { method: 'DELETE', requestId },
));
export const decideInboxMessage = createAcknowledgedThunk('uiData/decideInboxMessage', ({ inboxUrl, messageId, decision }, { requestId }) => requestAcknowledged(
  `${inboxUrl}/${encodeURIComponent(messageId)}/decision`,
  { method: 'POST', body: { decision }, requestId },
));
export const archiveInboxMessage = createAcknowledgedThunk('uiData/archiveInboxMessage', ({ inboxUrl, messageId, archived = true }, { requestId }) => requestAcknowledged(
  `${inboxUrl}/${encodeURIComponent(messageId)}/archive`,
  { method: 'POST', body: { archived }, requestId },
));

export const eventHandlers = {
  'inbox.snapshot': (state, payload) => {
    applyInboxSnapshot(state.inbox, payload);
  },
  'inbox.message.upserted': (state, payload) => {
    const message = payload.message;
    const messageId = typeof message?.id === 'string' ? message.id : null;
    if (!messageId) {
      return;
    }
    if (!state.inbox.byId[messageId]) {
      state.inbox.order.unshift(messageId);
    }
    state.inbox.byId[messageId] = { ...(state.inbox.byId[messageId] || {}), ...message };
  },
  'inbox.message.deleted': (state, payload) => {
    const messageId = payload.message_id;
    if (!messageId || !state.inbox.byId[messageId]) {
      return;
    }
    delete state.inbox.byId[messageId];
    state.inbox.order = state.inbox.order.filter((id) => id !== messageId);
  },
};

export function buildExtraReducers(builder) {
  builder
    .addCase(fetchInbox.pending, (state, action) => {
      state.inbox.loading = true;
      state.inbox.error = null;
      state.inbox.requestId = action.meta.requestId;
    })
    .addCase(fetchInbox.rejected, (state, action) => {
      if (state.inbox.requestId !== action.meta.requestId) {
        return;
      }
      state.inbox.loading = false;
      state.inbox.error = action.error?.message || 'Failed to load inbox';
      state.inbox.requestId = null;
    });
}

export const selectInboxMessages = createSelector(
  [(state) => state.uiData.inbox.order, (state) => state.uiData.inbox.byId],
  (order, byId) => order.map((messageId) => byId[messageId]).filter(Boolean),
);
export const selectInboxState = (state) => state.uiData.inbox;
export const selectInboxUnreadCount = createSelector(
  [selectInboxMessages],
  (messages) => messages.filter((message) => !message.read && !message.archived).length,
);
