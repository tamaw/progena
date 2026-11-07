import { createSelector } from '@reduxjs/toolkit';
import { UI_EVENT } from '../../realtime/eventVocabulary';
import { addStreamFailureLifecycle, createAcknowledgedThunk, requestAcknowledged } from '../../store/async';
import { withQuery } from '../../store/http';
import { failureMessage } from '../../lib/failures';
import { realtimeClientId, waitForRealtimeClient } from '../../realtime/realtimeClient';
import { createPositionalListOps } from '../../lib/positionalListCache';

export const INBOX_LIST_PAGE_SIZE = 50;

const {
  indexOfRow,
  invalidatePagesFrom,
  removeRowAt,
  settleInFlightRequest,
} = createPositionalListOps(INBOX_LIST_PAGE_SIZE);

const emptyActive = () => ({
  messageId: null,
  detail: null,
  loading: false,
  error: null,
  requestId: null,
});

export const initialState = {
  inbox: {
    byId: {},
    tombstones: {},
    view: { q: '', filter: 'all', sort: 'recent' },
    total: 0,
    rowsByIndex: {},
    loadedPages: {},
    inFlightRequests: {},
    rangeVersion: 0,
    rowDelta: null,
    loaded: false,
    loading: false,
    error: null,
    active: emptyActive(),
    counts: {
      unread: null,
      pendingApprovals: null,
      loading: false,
      error: null,
      requestId: null,
    },
  },
};

export function normalizeInboxView(view = {}) {
  return {
    q: typeof view.q === 'string' ? view.q : '',
    filter: ['all', 'unread', 'pending', 'archived'].includes(view.filter) ? view.filter : 'all',
    sort: view.sort === 'name' ? 'name' : 'recent',
  };
}

export function sameInboxView(left, right) {
  return left.q === right.q && left.filter === right.filter && left.sort === right.sort;
}

function slimInboxRow(row) {
  if (!row || typeof row !== 'object') return row;
  const { body: _body, ...slim } = row;
  return slim;
}

function messageIdFor(message) {
  return typeof message?.id === 'string' && message.id ? message.id : null;
}

function rowMatchesFilter(row, view) {
  if (!row) return false;
  if (view.filter === 'archived') return Boolean(row.archived);
  if (row.archived) return false;
  if (view.filter === 'unread') return !row.read;
  if (view.filter === 'pending') return Boolean(row.requires_approval) && !row.decision;
  return true;
}

function upsertCachedRow(inbox, row) {
  const messageId = messageIdFor(row);
  if (!messageId || inbox.tombstones[messageId]) return null;
  inbox.byId[messageId] = { ...(inbox.byId[messageId] || {}), ...slimInboxRow(row) };
  return messageId;
}
function applyInboxRowDelta(inbox, row) {
  const messageId = messageIdFor(row);
  if (!messageId || inbox.tombstones[messageId]) return;
  const indexValue = indexOfRow(inbox, messageId);
  const index = indexValue == null ? null : Number(indexValue);
  const previous = inbox.byId[messageId];
  upsertCachedRow(inbox, row);

  // The server's total includes rows outside our loaded windows. Never infer a
  // new count from a global row event whose position is unknown.
  if (index == null) {
    invalidatePagesFrom(inbox, 0);
    return;
  }
  if (!rowMatchesFilter(row, inbox.view)) {
    removeRowAt(inbox, index);
    inbox.total = Math.max(0, inbox.total - 1);
    return;
  }

  // Searches include the server-side body, name sort can move a row, and a
  // changed creation instant changes recency order.
  if (
    inbox.view.q
    || inbox.view.sort === 'name'
    || (inbox.view.sort === 'recent' && previous?.created_at !== row.created_at)
  ) {
    invalidatePagesFrom(inbox, 0);
  }
}

function applyInboxDelete(inbox, messageId) {
  if (!messageId) return;
  const indexValue = indexOfRow(inbox, messageId);
  if (indexValue != null) {
    removeRowAt(inbox, Number(indexValue));
    inbox.total = Math.max(0, inbox.total - 1);
  } else {
    invalidatePagesFrom(inbox, 0);
  }
  delete inbox.byId[messageId];
  inbox.tombstones[messageId] = true;
}

function inboxListUrl(inboxUrl, { view, offset, limit }) {
  return withQuery(inboxUrl, {
    client_id: realtimeClientId,
    q: view.q,
    filter: view.filter,
    sort: view.sort,
    offset: String(Math.max(0, offset)),
    limit: String(Math.max(0, Math.min(200, limit))),
  });
}

export const fetchInboxList = createAcknowledgedThunk(
  'uiData/fetchInboxList',
  async ({ inboxUrl, view, offset = 0, limit = INBOX_LIST_PAGE_SIZE }, { requestId }) => {
    await waitForRealtimeClient();
    return requestAcknowledged(
      inboxListUrl(inboxUrl, { view: normalizeInboxView(view), offset, limit }),
      { requestId },
    );
  },
);

export const requestInboxMessageDetail = createAcknowledgedThunk(
  'uiData/requestInboxMessageDetail',
  async ({ inboxUrl, messageId }, { requestId }) => {
    await waitForRealtimeClient();
    const acknowledgement = await requestAcknowledged(
      withQuery(`${inboxUrl}/${encodeURIComponent(messageId)}`, { client_id: realtimeClientId }),
      { requestId },
    );
    return { ...acknowledgement, messageId };
  },
);

export const fetchInboxCounts = createAcknowledgedThunk(
  'uiData/fetchInboxCounts',
  async ({ inboxUrl }, { requestId }) => {
    await waitForRealtimeClient();
    return requestAcknowledged(
      withQuery(`${inboxUrl}/counts`, { client_id: realtimeClientId }),
      { requestId },
    );
  },
);

export const setInboxMessageInterest = createAcknowledgedThunk(
  'uiData/setInboxMessageInterest',
  async ({ inboxUrl, messageId, watching }, { requestId }) => {
    await waitForRealtimeClient();
    return requestAcknowledged(`${inboxUrl}/subscription`, {
      method: 'PUT',
      body: {
        client_id: realtimeClientId,
        message_id: messageId || null,
        ...(watching === undefined ? {} : { watching }),
      },
      requestId,
    });
  },
);

export const markInboxMessageRead = createAcknowledgedThunk('uiData/markInboxMessageRead', async ({ inboxUrl, messageId, read = true }, { requestId }) => {
  await waitForRealtimeClient();
  return requestAcknowledged(
    withQuery(`${inboxUrl}/${encodeURIComponent(messageId)}/read`, { client_id: realtimeClientId }),
    { method: 'POST', body: { read }, requestId },
  );
});
export const deleteInboxMessage = createAcknowledgedThunk('uiData/deleteInboxMessage', async ({ inboxUrl, messageId }, { requestId }) => {
  await waitForRealtimeClient();
  return requestAcknowledged(
    withQuery(`${inboxUrl}/${encodeURIComponent(messageId)}`, { client_id: realtimeClientId }),
    { method: 'DELETE', requestId },
  );
});
export const decideInboxMessage = createAcknowledgedThunk('uiData/decideInboxMessage', async ({ inboxUrl, messageId, decision }, { requestId }) => {
  await waitForRealtimeClient();
  return requestAcknowledged(
    withQuery(`${inboxUrl}/${encodeURIComponent(messageId)}/decision`, { client_id: realtimeClientId }),
    { method: 'POST', body: { decision }, requestId },
  );
});
export const archiveInboxMessage = createAcknowledgedThunk('uiData/archiveInboxMessage', async ({ inboxUrl, messageId, archived = true }, { requestId }) => {
  await waitForRealtimeClient();
  return requestAcknowledged(
    withQuery(`${inboxUrl}/${encodeURIComponent(messageId)}/archive`, { client_id: realtimeClientId }),
    { method: 'POST', body: { archived }, requestId },
  );
});

export const eventHandlers = {
  [UI_EVENT.INBOX_LIST_LOADED]: (state, payload) => {
    const requestId = payload?.request_id;
    const request = requestId ? state.inbox.inFlightRequests[requestId] : null;
    if (
      !request
      || request.rangeVersion !== state.inbox.rangeVersion
      || !sameInboxView(normalizeInboxView(payload?.view), state.inbox.view)
    ) return;

    const rows = Array.isArray(payload?.rows) ? payload.rows : [];
    for (let index = request.offset; index < request.offset + request.limit; index += 1) {
      delete state.inbox.rowsByIndex[index];
    }
    for (const [index, row] of rows.entries()) {
      const messageId = upsertCachedRow(state.inbox, row);
      if (messageId) state.inbox.rowsByIndex[request.offset + index] = messageId;
    }
    if (Number.isInteger(payload?.total)) {
      state.inbox.total = payload.total;
      for (const index of Object.keys(state.inbox.rowsByIndex)) {
        if (Number(index) >= payload.total) delete state.inbox.rowsByIndex[index];
      }
    }
    state.inbox.loadedPages[request.offset] = true;
    delete state.inbox.inFlightRequests[requestId];
    state.inbox.loaded = true;
    state.inbox.loading = Object.keys(state.inbox.inFlightRequests).length > 0;
    state.inbox.error = null;
  },
  [UI_EVENT.INBOX_MESSAGE_SELECTED]: (state, payload) => {
    const messageId = messageIdFor(payload?.message);
    if (
      !messageId
      || messageId !== state.inbox.active.messageId
      || payload?.request_id !== state.inbox.active.requestId
    ) return;
    state.inbox.active.detail = payload.message;
    state.inbox.active.loading = false;
    state.inbox.active.error = null;
  },
  [UI_EVENT.INBOX_MESSAGE_UPDATED]: (state, payload) => {
    const messageId = messageIdFor(payload?.message);
    if (!messageId || messageId !== state.inbox.active.messageId) return;
    state.inbox.active.detail = payload.message;
    state.inbox.active.loading = false;
    state.inbox.active.error = null;
  },
  [UI_EVENT.INBOX_MESSAGE_UPSERTED]: (state, payload) => {
    applyInboxRowDelta(state.inbox, payload?.message);
  },
  [UI_EVENT.INBOX_MESSAGE_DELETED]: (state, payload) => {
    const messageId = typeof payload?.message_id === 'string' ? payload.message_id : null;
    applyInboxDelete(state.inbox, messageId);
    if (messageId === state.inbox.active.messageId) state.inbox.active = emptyActive();
  },
  [UI_EVENT.INBOX_COUNTS_UPDATED]: (state, payload) => {
    if (!Number.isInteger(payload?.unread) || payload.unread < 0) return;
    state.inbox.counts.unread = payload.unread;
    state.inbox.counts.pendingApprovals = Number.isInteger(payload?.pending_approvals)
      && payload.pending_approvals >= 0
      ? payload.pending_approvals
      : null;
    state.inbox.counts.loading = false;
    state.inbox.counts.error = null;
    state.inbox.counts.requestId = null;
  },
};

export function buildExtraReducers(builder) {
  builder
    .addCase(fetchInboxList.pending, (state, action) => {
      const { offset = 0, limit = INBOX_LIST_PAGE_SIZE } = action.meta.arg;
      const view = normalizeInboxView(action.meta.arg.view);
      if (!sameInboxView(view, state.inbox.view)) {
        state.inbox.byId = {};
        state.inbox.view = view;
        state.inbox.total = 0;
        state.inbox.rowsByIndex = {};
        state.inbox.loadedPages = {};
        state.inbox.inFlightRequests = {};
        state.inbox.rangeVersion += 1;
        state.inbox.rowDelta = null;
        state.inbox.loaded = false;
      }
      state.inbox.inFlightRequests[action.meta.requestId] = {
        offset: Math.max(0, offset),
        limit: Math.max(0, Math.min(200, limit)),
        rangeVersion: state.inbox.rangeVersion,
      };
      state.inbox.loading = !state.inbox.loaded;
      state.inbox.error = null;
    })
    .addCase(fetchInboxList.rejected, (state, action) => {
      if (!state.inbox.inFlightRequests[action.meta.requestId]) return;
      delete state.inbox.inFlightRequests[action.meta.requestId];
      state.inbox.loading = Object.keys(state.inbox.inFlightRequests).length > 0 && !state.inbox.loaded;
      state.inbox.error = failureMessage(action.error?.message ? action.error : 'Failed to load inbox').message;
    })
    .addCase(requestInboxMessageDetail.pending, (state, action) => {
      state.inbox.active.messageId = action.meta.arg.messageId;
      state.inbox.active.detail = null;
      state.inbox.active.loading = true;
      state.inbox.active.error = null;
      state.inbox.active.requestId = action.meta.requestId;
    })
    .addCase(requestInboxMessageDetail.rejected, (state, action) => {
      if (state.inbox.active.requestId !== action.meta.requestId) return;
      state.inbox.active.loading = false;
      state.inbox.active.error = failureMessage(action.error?.message ? action.error : 'Failed to load inbox message').message;
    })
    .addCase(fetchInboxCounts.pending, (state, action) => {
      state.inbox.counts.loading = true;
      state.inbox.counts.error = null;
      state.inbox.counts.requestId = action.meta.requestId;
    })
    .addCase(fetchInboxCounts.rejected, (state, action) => {
      if (state.inbox.counts.requestId !== action.meta.requestId) return;
      state.inbox.counts.loading = false;
      state.inbox.counts.error = failureMessage(action.error?.message ? action.error : 'Failed to load inbox counts').message;
      state.inbox.counts.requestId = null;
    });
  addStreamFailureLifecycle(builder, (state, requestId) => {
    settleInFlightRequest(state.inbox, requestId);
  });
}

export const selectInboxState = (state) => state.uiData.inbox;
export const selectInboxMessages = createSelector(
  [(state) => state.uiData.inbox.rowsByIndex, (state) => state.uiData.inbox.byId],
  (rowsByIndex, byId) => Object.entries(rowsByIndex)
    .sort(([left], [right]) => Number(left) - Number(right))
    .map(([, messageId]) => byId[messageId])
    .filter(Boolean),
);
export const selectInboxUnreadCount = (state) => state.uiData.inbox.counts.unread;
export const selectInboxPendingApprovals = (state) => state.uiData.inbox.counts.pendingApprovals;
