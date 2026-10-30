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
import { deriveChatStatus } from '../models/chatStatus';
import { failureMessage } from '../../../lib/failures';

const emptyActive = () => ({
  threadId: null,
  detail: null,
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
    order: [],
    loaded: false,
    loading: false,
    error: null,
    requestId: null,
    requestedAt: null,
    lastRequestTotal: null,
    active: emptyActive(),
    reconciliation: emptyReconciliation(),
  },
};

function threadIdFor(session) {
  return typeof session?.thread_id === 'string' && session.thread_id
    ? session.thread_id
    : null;
}

function replaceChatList(chats, sessions) {
  chats.byId = {};
  chats.order = [];
  for (const session of Array.isArray(sessions) ? sessions : []) {
    const threadId = threadIdFor(session);
    if (!threadId || chats.byId[threadId]) {
      continue;
    }
    chats.byId[threadId] = session;
    chats.order.push(threadId);
  }
}

function upsertChat(chats, session) {
  const threadId = threadIdFor(session);
  if (!threadId) {
    return;
  }
  if (!chats.byId[threadId]) {
    chats.order.unshift(threadId);
  }
  chats.byId[threadId] = {
    ...(chats.byId[threadId] || {}),
    ...session,
  };
}

function addChatIfMissing(chats, session) {
  const threadId = threadIdFor(session);
  if (!threadId || chats.byId[threadId]) {
    return;
  }
  chats.byId[threadId] = session;
  chats.order.unshift(threadId);
}

function deleteChatFromList(chats, threadId) {
  if (!threadId) {
    return;
  }
  delete chats.byId[threadId];
  chats.order = chats.order.filter((item) => item !== threadId);
}

function mergeActiveDetail(active, session) {
  if (!session?.thread_id) {
    return;
  }
  if (active.detail?.thread_id !== session.thread_id) {
    return;
  }
  active.detail = {
    ...active.detail,
    ...session,
    messages: active.detail.messages || [],
  };
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

export const fetchChatSessions = createAcknowledgedThunk('uiData/fetchChatSessions', async ({ chatsUrl, includeArchived = true }, { requestId }) => {
  return requestAcknowledged(
    withQuery(chatsUrl, { include_archived: includeArchived ? 'true' : 'false' }),
    { requestId },
  );
});
export const requestChatSessionDetail = createAcknowledgedThunk('uiData/requestChatSessionDetail', async ({ chatsUrl, threadId }, { requestId }) => {
  const acknowledgement = await requestAcknowledged(
    withQuery(`${chatsUrl}/${encodeURIComponent(threadId)}`, { request_id: requestId }),
    { requestId },
  );
  return { ...acknowledgement, threadId };
});
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
export const requestChatSessionReconciliation = createAcknowledgedThunk('uiData/requestChatSessionReconciliation', async ({ chatsUrl, threadId }, { requestId }) => {
  const acknowledgement = await requestAcknowledged(
    withQuery(`${chatsUrl}/${encodeURIComponent(threadId)}`, { request_id: requestId }),
    { requestId },
  );
  return { ...acknowledgement, threadId };
});
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
  },
};
export const eventHandlers = {
  'chat.response.started': (state, payload) => {
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
    // Tracked even for the thread this tab is currently viewing: a generation
    // started elsewhere still needs to be discoverable so a spectator stream
    // can attach to it.
    state.chatRuntime.generatingChatIds = addChatRuntimeId(
      state.chatRuntime.generatingChatIds,
      threadId,
    );
  },
  'chat.response.activity': (state, payload) => {
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
  'chat.response.completed': (state, payload) => {
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
  'chat.response.failed': (state, payload) => {
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
  'chat.response.cancelled': (state, payload) => {
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
  'chat.sessions.snapshot': (state, payload) => {
    replaceChatList(state.chats, payload.sessions);
    state.chats.loaded = true;
    if (!state.chats.loading || payload.request_id === state.chats.requestId) {
      state.chats.loading = false;
      state.chats.error = null;
      state.chats.requestId = null;
      state.chats.requestedAt = new Date().toISOString();
    }
  },
  'chat.session.created': (state, payload) => {
    upsertChat(state.chats, payload.session);
    mergeActiveDetail(state.chats.active, payload.session);
    state.chats.loaded = true;
  },
  'chat.session.metadata.updated': (state, payload) => {
    upsertChat(state.chats, payload.session);
    mergeActiveDetail(state.chats.active, payload.session);
    state.chats.loaded = true;
  },
  'chat.session.upserted': (state, payload) => {
    addChatIfMissing(state.chats, payload.session);
    state.chats.loaded = true;
  },
  'chat.session.selected': (state, payload) => {
    hydrateResponseTracking(state.chatRuntime, payload.session);
    if (state.chats.reconciliation.requestId === payload.request_id) {
      state.chats.reconciliation.detail = payload.session || null;
      state.chats.reconciliation.threadId = payload.session?.thread_id || null;
      state.chats.reconciliation.error = null;
      return;
    }
    if (state.chats.active.requestId && payload.request_id !== state.chats.active.requestId) {
      return;
    }
    state.chats.active.detail = payload.session || null;
    state.chats.active.threadId = payload.session?.thread_id || null;
    state.chats.active.loading = false;
    state.chats.active.error = null;
    state.chats.active.requestId = payload.request_id || state.chats.active.requestId;
    state.chats.active.hasMoreMessages = Boolean(payload.session?.has_more_messages);
    state.chats.active.pendingOlderPage = null;
  },
  'chat.messages.page.loaded': (state, payload) => {
    if (payload?.thread_id !== state.chats.active.threadId) {
      return;
    }
    state.chats.active.hasMoreMessages = Boolean(payload.has_more);
    state.chats.active.pendingOlderPage = {
      requestId: payload.request_id || null,
      messages: Array.isArray(payload.messages) ? payload.messages : [],
      summaryMarkers: Array.isArray(payload.summary_markers) ? payload.summary_markers : [],
    };
  },
  'chat.session.deleted': (state, payload) => {
    const threadId = typeof payload?.thread_id === 'string' && payload.thread_id
      ? payload.thread_id
      : null;
    deleteChatFromList(state.chats, threadId);
    if (threadId) {
      clearResponseTrackingForThread(state.chatRuntime, threadId);
      state.chatRuntime.generatingChatIds = removeChatRuntimeId(state.chatRuntime.generatingChatIds, threadId);
      state.chatRuntime.unreadChatIds = removeChatRuntimeId(state.chatRuntime.unreadChatIds, threadId);
      state.chatRuntime.erroredChatIds = removeChatRuntimeId(state.chatRuntime.erroredChatIds, threadId);
    }
    if (state.chats.active.threadId === threadId) {
      state.chats.active = emptyActive();
    }
  },
};

export function buildExtraReducers(builder) {
  builder
    .addCase(fetchChatSessions.pending, (state, action) => {
      state.chats.loading = true;
      state.chats.error = null;
      state.chats.requestId = action.meta.requestId;
    })
    .addCase(fetchChatSessions.rejected, (state, action) => {
      if (state.chats.requestId !== action.meta.requestId) {
        return;
      }
      state.chats.loading = false;
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
