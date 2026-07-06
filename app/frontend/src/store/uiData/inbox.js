/**
 * Inbox domain: agent-to-user messages written as /inbox/<id>.md files in the
 * runtime, kept live by `inbox.snapshot` / `inbox.message.upserted` /
 * `inbox.message.deleted` events on /api/ui/stream. Snapshots carry full
 * markdown bodies (messages are small), so there is no detail request dance.
 */
import { createAsyncThunk, createSelector } from '@reduxjs/toolkit';
import { requestJson } from '../http';

export const initialState = {
  inbox: {
    byId: {},
    order: [],
    loaded: false,
    loading: false,
    error: null,
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

// Shared by the SSE event handler and the GET thunk's own fulfilled case, so
// a page renders directly from the HTTP response instead of only becoming
// "loaded" once the backend's echoed inbox.snapshot event happens to arrive
// (which would otherwise never resolve if the realtime stream is down).
function applyInboxSnapshot(inbox, payload) {
  replaceMessageList(inbox, payload?.messages);
  inbox.loaded = true;
  inbox.loading = false;
  inbox.error = null;
}

export const fetchInbox = createAsyncThunk(
  'uiData/fetchInbox',
  ({ inboxUrl }) => requestJson(inboxUrl, { errorLabel: 'Failed to load inbox' }),
);

export const markInboxMessageRead = createAsyncThunk(
  'uiData/markInboxMessageRead',
  ({ inboxUrl, messageId, read = true }) => requestJson(
    `${inboxUrl}/${encodeURIComponent(messageId)}/read`,
    {
      method: 'POST',
      body: { read },
      errorLabel: 'Failed to update inbox message',
    },
  ),
);

export const deleteInboxMessage = createAsyncThunk(
  'uiData/deleteInboxMessage',
  ({ inboxUrl, messageId }) => requestJson(
    `${inboxUrl}/${encodeURIComponent(messageId)}`,
    {
      method: 'DELETE',
      errorLabel: 'Failed to delete inbox message',
    },
  ),
);

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
    .addCase(fetchInbox.pending, (state) => {
      state.inbox.loading = true;
      state.inbox.error = null;
    })
    .addCase(fetchInbox.fulfilled, (state, action) => {
      applyInboxSnapshot(state.inbox, action.payload);
    })
    .addCase(fetchInbox.rejected, (state, action) => {
      state.inbox.loading = false;
      state.inbox.error = action.error?.message || 'Failed to load inbox';
    })
    // Optimistic read/unread flip; the snapshot that follows the POST (or the
    // store-change watcher) reconciles the true state either way.
    .addCase(markInboxMessageRead.pending, (state, action) => {
      const { messageId, read = true } = action.meta.arg;
      const message = state.inbox.byId[messageId];
      if (message) {
        message.read = read;
      }
    })
    // Optimistic removal; a failed DELETE is reconciled by the next snapshot.
    .addCase(deleteInboxMessage.pending, (state, action) => {
      const { messageId } = action.meta.arg;
      if (state.inbox.byId[messageId]) {
        delete state.inbox.byId[messageId];
        state.inbox.order = state.inbox.order.filter((id) => id !== messageId);
      }
    });
}

export const selectInboxMessages = createSelector(
  [(state) => state.uiData.inbox.order, (state) => state.uiData.inbox.byId],
  (order, byId) => order.map((messageId) => byId[messageId]).filter(Boolean),
);
export const selectInboxState = (state) => state.uiData.inbox;
export const selectInboxUnreadCount = createSelector(
  [selectInboxMessages],
  (messages) => messages.filter((message) => !message.read).length,
);
