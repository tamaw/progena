/**
 * Chats domain: the conversation roster, the active (selected) chat detail, and
 * the per-chat runtime indicators (unread / response activity / generating /
 * errored) that drive conversation cards and the primary nav dot. Kept live by
 * `chat.*` events on /api/ui/stream; generation indicators are also set
 * imperatively by the chat stream hook so route changes do not clear them.
 */
import { createSelector } from '@reduxjs/toolkit';
import { createAcknowledgedThunk, requestAcknowledged } from '../../../store/async';
import { withQuery } from '../../../store/http';
import { realtimeClientId, waitForRealtimeClient } from '../../../realtime/realtimeClient';
import { deriveChatStatus } from '../models/chatStatus';
import { failureMessage } from '../../../lib/failures';
import { UI_EVENT } from '../../../realtime/eventVocabulary';
import { forgetServerRegistration, noteServerRegistration } from '../backgroundTaskSubscription';

// Opening a chat installs this tab's background-task interest server-side.
// Its stream event can arrive before the HTTP acknowledgement, so the
// registration is noted up front and withdrawn if the request fails.
async function requestChatDetail(chatsUrl, threadId, requestId) {
  await waitForRealtimeClient();
  const chatUrl = `${chatsUrl}/${encodeURIComponent(threadId)}`;
  const tasksUrl = `${chatUrl}/background-tasks`;
  noteServerRegistration(tasksUrl);
  try {
    const acknowledgement = await requestAcknowledged(
      withQuery(chatUrl, { client_id: realtimeClientId }),
      { requestId },
    );
    return { ...acknowledgement, threadId };
  } catch (error) {
    forgetServerRegistration(tasksUrl);
    throw error;
  }
}

const emptyActive = () => ({
  threadId: null,
  detail: null,
  queue: null,
  loading: false,
  error: null,
  requestId: null,
  hasMoreMessages: false,
  pendingOlderPage: null,
});

const emptyReconciliation = () => ({
  threadId: null,
  detail: null,
  error: null,
  requestId: null,
});

export const initialState = {
  chatRuntime: {
    unreadChatIds: [],
    generatingChatIds: [],
    erroredChatIds: [],
    activityByThreadId: {},
    responseThreadsByRequestId: {},
    requestIdByThreadId: {},
  },
  chats: {
    byId: {},
    tombstones: {},
    view: { q: '', sort: 'recent', filter: 'active' },
    order: [],
    total: 0,
    nextCursor: null,
    loaded: false,
    loading: false,
    loadingMore: false,
    error: null,
    requestId: null,
    lookupRequestIds: {},
    lookupResults: {},
    sourceTotals: {},
    requestedAt: null,
    active: emptyActive(),
    reconciliation: emptyReconciliation(),
  },
};

function threadIdFor(session) {
  return typeof session?.thread_id === 'string' && session.thread_id
    ? session.thread_id
    : null;
}

function normalizeView(view = {}) {
  return {
    q: typeof view.q === 'string' ? view.q : '',
    sort: view.sort === 'name' ? 'name' : 'recent',
    filter: view.filter === 'archived' || view.filter === 'all' ? view.filter : 'active',
  };
}

function sameView(left, right) {
  return left.q === right.q && left.sort === right.sort && left.filter === right.filter;
}
function rowMatchesFilter(row, view) {
  return Boolean(
    row
    && (view.filter !== 'active' || !row.archived)
    && (view.filter !== 'archived' || row.archived),
  );
}


function rowMatchesView(row, view) {
  if (!rowMatchesFilter(row, view)) {
    return false;
  }
  const query = view.q.trim().toLocaleLowerCase();
  if (!query) return true;
  return `${row.title || ''} ${row.preview || ''}`.toLocaleLowerCase().includes(query);
}

function compareRows(left, right, sort) {
  if (sort === 'name') {
    const title = String(left?.title || '').localeCompare(String(right?.title || ''), undefined, { sensitivity: 'base' });
    return title || String(left?.thread_id || '').localeCompare(String(right?.thread_id || ''));
  }
  const pinned = Number(Boolean(right?.pinned)) - Number(Boolean(left?.pinned));
  if (pinned) return pinned;
  const activity = String(right?.last_activity || '').localeCompare(String(left?.last_activity || ''));
  return activity || String(left?.thread_id || '').localeCompare(String(right?.thread_id || ''));
}

function sortOrder(chats) {
  chats.order.sort((leftId, rightId) => compareRows(chats.byId[leftId], chats.byId[rightId], chats.view.sort));
}

function upsertCachedRow(chats, row) {
  const threadId = threadIdFor(row);
  if (!threadId || chats.tombstones[threadId]) return null;
  chats.byId[threadId] = { ...(chats.byId[threadId] || {}), ...row };
  return threadId;
}

function replaceChatList(chats, rows) {
  const order = [];
  for (const row of Array.isArray(rows) ? rows : []) {
    const threadId = upsertCachedRow(chats, row);
    if (threadId && !order.includes(threadId)) order.push(threadId);
  }
  chats.order = order;
}

function applyRowDelta(chats, row) {
  const threadId = threadIdFor(row);
  if (!threadId || chats.tombstones[threadId]) return;
  const wasInView = chats.order.includes(threadId);
  const previous = chats.byId[threadId];
  const previouslyMatched = wasInView || rowMatchesView(previous, chats.view);
  const matches = wasInView ? rowMatchesFilter(row, chats.view) : rowMatchesView(row, chats.view);
  upsertCachedRow(chats, row);

  if (wasInView && !matches) {
    chats.order = chats.order.filter((id) => id !== threadId);
  } else if (!wasInView && matches) {
    chats.order.push(threadId);
    sortOrder(chats);
    if (chats.nextCursor && chats.order.indexOf(threadId) >= chats.order.length - 1) {
      chats.order = chats.order.filter((id) => id !== threadId);
    }
  } else if (wasInView) {
    sortOrder(chats);
  }
  if (!previouslyMatched && matches) chats.total += 1;
  if (previouslyMatched && !matches) chats.total = Math.max(0, chats.total - 1);
}

function deleteChatFromList(chats, threadId) {
  if (!threadId) return;
  const wasMatched = rowMatchesView(chats.byId[threadId], chats.view);
  delete chats.byId[threadId];
  chats.tombstones[threadId] = true;
  chats.order = chats.order.filter((item) => item !== threadId);
  if (wasMatched) chats.total = Math.max(0, chats.total - 1);
}

function mergeActiveDetail(active, session) {
  if (!session?.thread_id || active.detail?.thread_id !== session.thread_id) return;
  active.detail = { ...active.detail, ...session, messages: active.detail.messages || [] };
}

function normalizeChatRuntimeIds(ids) {
  if (!Array.isArray(ids)) {
    return [];
  }
  return [...new Set(ids.filter((id) => typeof id === 'string' && id))];
}

function addChatRuntimeId(ids, threadId) {
  if (typeof threadId !== 'string' || !threadId || ids.includes(threadId)) {
    return ids;
  }
  return [...ids, threadId];
}

function removeChatRuntimeId(ids, threadId) {
  if (typeof threadId !== 'string' || !threadId || !ids.includes(threadId)) {
    return ids;
  }
  return ids.filter((id) => id !== threadId);
}

function responseRequestIdFor(payload) {
  if (
    (payload?.queued === true || payload?.queue_execution === true)
    && typeof payload?.content_request_id === 'string'
    && payload.content_request_id
  ) {
    return payload.content_request_id;
  }
  return typeof payload?.request_id === 'string' && payload.request_id ? payload.request_id : null;
}

function responseThreadIdFor(payload, responseThreadsByRequestId) {
  if (typeof payload?.thread_id === 'string' && payload.thread_id) {
    return payload.thread_id;
  }
  const requestId = responseRequestIdFor(payload);
  return requestId ? responseThreadsByRequestId[requestId] || null : null;
}

function responseActivityFor(value) {
  return value === 'delegating' || value === 'generating' ? value : null;
}

function clearResponseTrackingForThread(runtime, threadId) {
  const requestId = runtime.requestIdByThreadId[threadId];
  if (requestId && runtime.responseThreadsByRequestId[requestId] === threadId) {
    delete runtime.responseThreadsByRequestId[requestId];
  }
  delete runtime.requestIdByThreadId[threadId];
  delete runtime.activityByThreadId[threadId];
}

function beginResponseTracking(runtime, requestId, threadId) {
  const previousRequestId = runtime.requestIdByThreadId[threadId];
  if (previousRequestId && previousRequestId !== requestId) {
    if (runtime.responseThreadsByRequestId[previousRequestId] === threadId) {
      delete runtime.responseThreadsByRequestId[previousRequestId];
    }
    delete runtime.activityByThreadId[threadId];
  }
  runtime.responseThreadsByRequestId[requestId] = threadId;
  runtime.requestIdByThreadId[threadId] = requestId;
}

function terminalResponseFor(payload, runtime) {
  const requestId = responseRequestIdFor(payload);
  const threadId = responseThreadIdFor(payload, runtime.responseThreadsByRequestId);
  if (
    !requestId
    || !threadId
    || runtime.requestIdByThreadId[threadId] !== requestId
  ) {
    return null;
  }
  return { requestId, threadId };
}

function clearTerminalResponseTracking(runtime, { requestId, threadId }) {
  if (runtime.responseThreadsByRequestId[requestId] === threadId) {
    delete runtime.responseThreadsByRequestId[requestId];
  }
  delete runtime.requestIdByThreadId[threadId];
  delete runtime.activityByThreadId[threadId];
}

function hydrateResponseTracking(runtime, session) {
  const threadId = threadIdFor(session);
  const requestId = typeof session?.active_request_id === 'string' && session.active_request_id
    ? session.active_request_id
    : null;
  if (!threadId) {
    return;
  }
  if (!requestId) {
    clearResponseTrackingForThread(runtime, threadId);
    runtime.generatingChatIds = removeChatRuntimeId(runtime.generatingChatIds, threadId);
    return;
  }

  beginResponseTracking(runtime, requestId, threadId);
  runtime.generatingChatIds = addChatRuntimeId(runtime.generatingChatIds, threadId);
  const activity = responseActivityFor(session?.activity);
  if (activity) {
    runtime.activityByThreadId[threadId] = activity;
  } else {
    delete runtime.activityByThreadId[threadId];
  }
}

function chatListUrl(chatsUrl, { view, cursor, limit = 50, source, threadIds = [] }) {
  const params = new URLSearchParams({
    client_id: realtimeClientId,
    q: view.q,
    sort: view.sort,
    filter: view.filter,
    limit: String(Math.max(0, Math.min(200, limit))),
  });
  if (cursor) params.set('cursor', cursor);
  if (source) params.set('source', source);
  for (const threadId of threadIds) {
    if (typeof threadId === 'string' && threadId) params.append('thread_id', threadId);
  }
  return `${chatsUrl}${chatsUrl.includes('?') ? '&' : '?'}${params.toString()}`;
}

export const fetchChatSessions = createAcknowledgedThunk(
  'uiData/fetchChatSessions',
  async ({ chatsUrl, view, cursor = null, limit = 50, source = null, threadIds = [], lookup = false }, { requestId }) => {
    await waitForRealtimeClient();
    return requestAcknowledged(
      chatListUrl(chatsUrl, {
        view: normalizeView(view),
        cursor,
        limit,
        source,
        threadIds,
        lookup,
      }),
      { requestId },
    );
  },
);
export const requestChatSessionDetail = createAcknowledgedThunk(
  'uiData/requestChatSessionDetail',
  ({ chatsUrl, threadId }, { requestId }) => requestChatDetail(chatsUrl, threadId, requestId),
);
export const fetchChatMessages = createAcknowledgedThunk('uiData/fetchChatMessages', async ({ chatsUrl, threadId, beforeMessageId, limit }, { requestId }) => {
  const acknowledgement = await requestAcknowledged(
    withQuery(`${chatsUrl}/${encodeURIComponent(threadId)}/messages`, {
      ...(beforeMessageId ? { before_message_id: beforeMessageId } : {}),
      ...(limit ? { limit: String(limit) } : {}),
    }),
    { requestId },
  );
  return { ...acknowledgement, threadId };
});

// A completed foreground stream is already visible locally. This fetches the
// persisted canonical transcript without taking ownership of the selected
// detail or putting ConversationPage into its blocking loading branch.
export const requestChatSessionReconciliation = createAcknowledgedThunk(
  'uiData/requestChatSessionReconciliation',
  ({ chatsUrl, threadId }, { requestId }) => requestChatDetail(chatsUrl, threadId, requestId),
);
export const updateChatSessionMetadata = createAcknowledgedThunk('uiData/updateChatSessionMetadata', async ({ chatsUrl, threadId, patch }, { requestId }) => {
  const acknowledgement = await requestAcknowledged(
    `${chatsUrl}/${encodeURIComponent(threadId)}`,
    { method: 'PATCH', body: patch, requestId },
  );
  return { ...acknowledgement, threadId };
});
export const deleteChatSession = createAcknowledgedThunk('uiData/deleteChatSession', async ({ chatsUrl, threadId }, { requestId }) => {
  const acknowledgement = await requestAcknowledged(
    `${chatsUrl}/${encodeURIComponent(threadId)}`,
    { method: 'DELETE', requestId },
  );
  return { ...acknowledgement, threadId };
});

export const reducers = {
  setUnreadChatIds(state, action) {
    state.chatRuntime.unreadChatIds = normalizeChatRuntimeIds(action.payload);
  },
  markChatUnread(state, action) {
    state.chatRuntime.unreadChatIds = addChatRuntimeId(state.chatRuntime.unreadChatIds, action.payload);
  },
  clearChatUnread(state, action) {
    state.chatRuntime.unreadChatIds = removeChatRuntimeId(state.chatRuntime.unreadChatIds, action.payload);
  },
  setGeneratingChatIds(state, action) {
    state.chatRuntime.generatingChatIds = normalizeChatRuntimeIds(action.payload);
  },
  markChatGenerating(state, action) {
    state.chatRuntime.generatingChatIds = addChatRuntimeId(state.chatRuntime.generatingChatIds, action.payload);
  },
  clearChatGenerating(state, action) {
    state.chatRuntime.generatingChatIds = removeChatRuntimeId(state.chatRuntime.generatingChatIds, action.payload);
  },
  setErroredChatIds(state, action) {
    state.chatRuntime.erroredChatIds = normalizeChatRuntimeIds(action.payload);
  },
  markChatErrored(state, action) {
    state.chatRuntime.erroredChatIds = addChatRuntimeId(state.chatRuntime.erroredChatIds, action.payload);
  },
  clearChatErrored(state, action) {
    state.chatRuntime.erroredChatIds = removeChatRuntimeId(state.chatRuntime.erroredChatIds, action.payload);
  },
  clearActiveChatSession(state) {
    state.chats.active = emptyActive();
  },
  reconcileChatPagination(state, action) {
    const { threadId, requestId, preserveOlderMessages } = action.payload;
    const reconciliation = state.chats.reconciliation;
    if (
      preserveOlderMessages
      || state.chats.active.threadId !== threadId
      || reconciliation.threadId !== threadId
      || reconciliation.requestId !== requestId
      || !reconciliation.detail
    ) {
      return;
    }
    state.chats.active.hasMoreMessages = Boolean(reconciliation.detail.has_more_messages);
  },
  // Optimistic bump so the sidebar re-sorts a chat to the top of "Recent" the
  // instant a message is sent, instead of waiting for the stream to finish and
  // a fresh chat list snapshot to arrive.
  touchChatSession(state, action) {
    const threadId = action.payload;
    if (typeof threadId !== 'string' || !threadId || !state.chats.byId[threadId]) {
      return;
    }
    state.chats.byId[threadId] = {
      ...state.chats.byId[threadId],
      last_activity: new Date().toISOString(),
    };
    if (state.chats.order.includes(threadId)) sortOrder(state.chats);
  },
};
export const eventHandlers = {
  [UI_EVENT.CHAT_RESPONSE_STARTED]: (state, payload) => {
    const requestId = responseRequestIdFor(payload);
    const threadId = typeof payload?.thread_id === 'string' && payload.thread_id
      ? payload.thread_id
      : null;
    if (!requestId || !threadId) {
      return;
    }

    beginResponseTracking(state.chatRuntime, requestId, threadId);
    state.chatRuntime.unreadChatIds = removeChatRuntimeId(state.chatRuntime.unreadChatIds, threadId);
    state.chatRuntime.erroredChatIds = removeChatRuntimeId(state.chatRuntime.erroredChatIds, threadId);
    // The restart interruption describes the conversation's latest turn. A new
    // turn supersedes it; the interrupted turn itself keeps its own row once
    // the canonical transcript reports it `interrupted`.
    if (state.chats.active.threadId === threadId && state.chats.active.detail?.interruption) {
      state.chats.active.detail.interruption = null;
    }
    // Tracked even for the thread this tab is currently viewing: a generation
    // started elsewhere still needs to be discoverable so a spectator stream
    // can attach to it.
    state.chatRuntime.generatingChatIds = addChatRuntimeId(
      state.chatRuntime.generatingChatIds,
      threadId,
    );
  },
  [UI_EVENT.CHAT_RESPONSE_ACTIVITY]: (state, payload) => {
    const requestId = responseRequestIdFor(payload);
    const threadId = typeof payload?.thread_id === 'string' && payload.thread_id
      ? payload.thread_id
      : null;
    const activity = responseActivityFor(payload?.activity);
    if (
      !requestId
      || !threadId
      || !activity
      || state.chatRuntime.requestIdByThreadId[threadId] !== requestId
      || state.chatRuntime.activityByThreadId[threadId] === activity
    ) {
      return;
    }
    state.chatRuntime.activityByThreadId[threadId] = activity;
  },
  [UI_EVENT.CHAT_RESPONSE_COMPLETED]: (state, payload) => {
    const response = terminalResponseFor(payload, state.chatRuntime);
    if (!response) {
      return;
    }
    const { threadId } = response;
    clearTerminalResponseTracking(state.chatRuntime, response);
    state.chatRuntime.generatingChatIds = removeChatRuntimeId(
      state.chatRuntime.generatingChatIds,
      threadId,
    );
    if (state.chats.active.threadId !== threadId) {
      state.chatRuntime.unreadChatIds = addChatRuntimeId(state.chatRuntime.unreadChatIds, threadId);
    }
    state.chatRuntime.erroredChatIds = removeChatRuntimeId(state.chatRuntime.erroredChatIds, threadId);
  },
  [UI_EVENT.CHAT_RESPONSE_FAILED]: (state, payload) => {
    const response = terminalResponseFor(payload, state.chatRuntime);
    if (!response) {
      return;
    }
    const { threadId } = response;
    const isActiveThread = state.chats.active.threadId === threadId;
    clearTerminalResponseTracking(state.chatRuntime, response);
    state.chatRuntime.generatingChatIds = removeChatRuntimeId(
      state.chatRuntime.generatingChatIds,
      threadId,
    );
    if (isActiveThread) {
      state.chatRuntime.unreadChatIds = removeChatRuntimeId(state.chatRuntime.unreadChatIds, threadId);
      state.chatRuntime.erroredChatIds = removeChatRuntimeId(state.chatRuntime.erroredChatIds, threadId);
    } else {
      state.chatRuntime.unreadChatIds = addChatRuntimeId(state.chatRuntime.unreadChatIds, threadId);
      state.chatRuntime.erroredChatIds = addChatRuntimeId(state.chatRuntime.erroredChatIds, threadId);
    }
  },
  [UI_EVENT.CHAT_RESPONSE_CANCELLED]: (state, payload) => {
    const response = terminalResponseFor(payload, state.chatRuntime);
    if (!response) {
      return;
    }
    const { threadId } = response;
    clearTerminalResponseTracking(state.chatRuntime, response);
    state.chatRuntime.generatingChatIds = removeChatRuntimeId(
      state.chatRuntime.generatingChatIds,
      threadId,
    );
  },
  [UI_EVENT.CHAT_LIST_LOADED]: (state, payload) => {
    const requestId = payload?.request_id;
    const isLookup = Boolean(requestId && state.chats.lookupRequestIds[requestId]);
    if (!requestId || (!isLookup && requestId !== state.chats.requestId)) return;
    const rows = Array.isArray(payload?.rows) ? payload.rows : [];
    if (isLookup) {
      for (const row of rows) upsertCachedRow(state.chats, row);
      const lookup = state.chats.lookupRequestIds[requestId];
      for (const threadId of lookup.threadIds) {
        state.chats.lookupResults[threadId] = rows.some((row) => row?.thread_id === threadId);
      }
      if (lookup.source) state.chats.sourceTotals[lookup.source] = Number.isInteger(payload?.total) ? payload.total : 0;
      delete state.chats.lookupRequestIds[requestId];
      return;
    }
    if (!sameView(normalizeView(payload.view), state.chats.view)) return;
    replaceChatList(state.chats, rows);
    state.chats.total = Number.isInteger(payload?.total) ? payload.total : 0;
    state.chats.nextCursor = typeof payload?.next_cursor === 'string' ? payload.next_cursor : null;
    state.chats.loaded = true;
    state.chats.loading = false;
    state.chats.loadingMore = false;
    state.chats.error = null;
    state.chats.requestId = null;
    state.chats.requestedAt = new Date().toISOString();
  },
  [UI_EVENT.CHAT_LIST_PAGE]: (state, payload) => {
    const requestId = payload?.request_id;
    if (!requestId || requestId !== state.chats.requestId || !sameView(normalizeView(payload.view), state.chats.view)) return;
    for (const row of Array.isArray(payload?.rows) ? payload.rows : []) {
      const threadId = upsertCachedRow(state.chats, row);
      if (threadId && !state.chats.order.includes(threadId)) state.chats.order.push(threadId);
    }
    state.chats.total = Number.isInteger(payload?.total) ? payload.total : state.chats.total;
    state.chats.nextCursor = typeof payload?.next_cursor === 'string' ? payload.next_cursor : null;
    state.chats.loadingMore = false;
    state.chats.error = null;
    state.chats.requestId = null;
  },
  [UI_EVENT.CHAT_LIST_ROW_UPSERTED]: (state, payload) => {
    applyRowDelta(state.chats, payload?.row);
    mergeActiveDetail(state.chats.active, payload?.row);
  },
  [UI_EVENT.CHAT_LIST_ROW_DELETED]: (state, payload) => {
    const threadId = typeof payload?.thread_id === 'string' && payload.thread_id ? payload.thread_id : null;
    deleteChatFromList(state.chats, threadId);
    if (threadId) {
      clearResponseTrackingForThread(state.chatRuntime, threadId);
      state.chatRuntime.generatingChatIds = removeChatRuntimeId(state.chatRuntime.generatingChatIds, threadId);
      state.chatRuntime.unreadChatIds = removeChatRuntimeId(state.chatRuntime.unreadChatIds, threadId);
      state.chatRuntime.erroredChatIds = removeChatRuntimeId(state.chatRuntime.erroredChatIds, threadId);
    }
    if (state.chats.active.threadId === threadId) state.chats.active = emptyActive();
  },
  [UI_EVENT.CHAT_SESSION_SELECTED]: (state, payload) => {
    hydrateResponseTracking(state.chatRuntime, payload.session);
    if (state.chats.reconciliation.requestId === payload.request_id) {
      state.chats.reconciliation.detail = payload.session || null;
      state.chats.reconciliation.threadId = payload.session?.thread_id || null;
      state.chats.reconciliation.error = null;
      return;
    }
    if (state.chats.active.requestId && payload.request_id !== state.chats.active.requestId) return;
    state.chats.active.detail = payload.session || null;
    state.chats.active.queue = payload.queue || null;
    state.chats.active.threadId = payload.session?.thread_id || null;
    state.chats.active.loading = false;
    state.chats.active.error = null;
    state.chats.active.requestId = payload.request_id || state.chats.active.requestId;
    state.chats.active.hasMoreMessages = Boolean(payload.session?.has_more_messages);
    state.chats.active.pendingOlderPage = null;
  },
  [UI_EVENT.CHAT_MESSAGES_PAGE_LOADED]: (state, payload) => {
    if (payload?.thread_id !== state.chats.active.threadId) return;
    state.chats.active.hasMoreMessages = Boolean(payload.has_more);
    state.chats.active.pendingOlderPage = {
      requestId: payload.request_id || null,
      messages: Array.isArray(payload.messages) ? payload.messages : [],
      summaryMarkers: Array.isArray(payload.summary_markers) ? payload.summary_markers : [],
    };
  },
};

export function buildExtraReducers(builder) {
  builder
    .addCase(fetchChatSessions.pending, (state, action) => {
      const { cursor = null, lookup = false, threadIds = [], source = null, view } = action.meta.arg;
      if (lookup) {
        state.chats.lookupRequestIds[action.meta.requestId] = { threadIds, source };
        return;
      }
      state.chats.requestId = action.meta.requestId;
      state.chats.error = null;
      if (cursor) {
        state.chats.loadingMore = true;
        return;
      }
      state.chats.view = normalizeView(view);
      state.chats.order = [];
      state.chats.total = 0;
      state.chats.nextCursor = null;
      state.chats.loaded = false;
      state.chats.loading = true;
      state.chats.loadingMore = false;
    })
    .addCase(fetchChatSessions.rejected, (state, action) => {
      if (state.chats.lookupRequestIds[action.meta.requestId]) {
        delete state.chats.lookupRequestIds[action.meta.requestId];
        return;
      }
      if (state.chats.requestId !== action.meta.requestId) return;
      state.chats.loading = false;
      state.chats.loadingMore = false;
      state.chats.error = failureMessage(action.error?.message ? action.error : 'Failed to load chats').message;
      state.chats.requestId = null;
    })
    .addCase(requestChatSessionDetail.pending, (state, action) => {
      state.chats.active.threadId = action.meta.arg.threadId;
      state.chats.active.loading = true;
      state.chats.active.error = null;
      state.chats.active.requestId = action.meta.requestId;
    })
    .addCase(requestChatSessionDetail.rejected, (state, action) => {
      if (state.chats.active.requestId === action.meta.requestId) {
        state.chats.active.loading = false;
        state.chats.active.error = failureMessage(action.error?.message ? action.error : 'Failed to load chat').message;
      }
    })
    .addCase(requestChatSessionReconciliation.pending, (state, action) => {
      state.chats.reconciliation.threadId = action.meta.arg.threadId;
      state.chats.reconciliation.detail = null;
      state.chats.reconciliation.error = null;
      state.chats.reconciliation.requestId = action.meta.requestId;
    })
    .addCase(requestChatSessionReconciliation.rejected, (state, action) => {
      if (state.chats.reconciliation.requestId === action.meta.requestId) {
        state.chats.reconciliation.error = failureMessage(action.error?.message ? action.error : 'Failed to reconcile chat').message;
        state.chats.reconciliation.requestId = null;
      }
    });
}

export const selectChatSessions = createSelector(
  [(state) => state.uiData.chats.order, (state) => state.uiData.chats.byId],
  (order, byId) => order.map((threadId) => byId[threadId]).filter(Boolean),
);
export const selectChatSessionsState = (state) => state.uiData.chats;
export const selectChatRowsById = (state) => state.uiData.chats.byId;
export const selectChatLookupResults = (state) => state.uiData.chats.lookupResults;
export const selectChatSourceTotals = (state) => state.uiData.chats.sourceTotals;
export const selectUnreadChatIds = (state) => state.uiData.chatRuntime.unreadChatIds;
export const selectGeneratingChatIds = (state) => state.uiData.chatRuntime.generatingChatIds;
export const selectChatActivityByThreadId = (state) => state.uiData.chatRuntime.activityByThreadId;
export const selectChatActivityForThread = (state, threadId) => (
  threadId ? state.uiData.chatRuntime.activityByThreadId[threadId] || null : null
);
export const selectRequestIdByThreadId = (state) => state.uiData.chatRuntime.requestIdByThreadId;
export const selectRequestIdForThread = (state, threadId) => (
  threadId ? state.uiData.chatRuntime.requestIdByThreadId[threadId] || null : null
);
export const selectErroredChatIds = (state) => state.uiData.chatRuntime.erroredChatIds;
export const selectChatIndicators = createSelector(
  [selectUnreadChatIds, selectGeneratingChatIds, selectErroredChatIds, selectChatActivityByThreadId],
  (unreadChatIds, generatingChatIds, erroredChatIds, activityByThreadId) => {
    const hasUnread = unreadChatIds.length > 0;
    const isGenerating = generatingChatIds.length > 0;
    const isDelegating = Object.values(activityByThreadId).includes('delegating');
    const hasError = erroredChatIds.length > 0;
    return {
      hasUnread,
      isDelegating,
      isGenerating,
      hasError,
      status: deriveChatStatus({
        unread: hasUnread,
        delegating: isDelegating,
        generating: isGenerating,
        errored: hasError,
      }),
    };
  },
);
export const selectActiveChatSession = (state) => state.uiData.chats.active.detail;
export const selectActiveChatSessionState = (state) => state.uiData.chats.active;

export const selectChatReconciliationState = (state) => state.uiData.chats.reconciliation;
