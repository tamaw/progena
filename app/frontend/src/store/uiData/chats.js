/**
 * Chats domain: the conversation roster, the active (selected) chat detail, and
 * the per-chat runtime indicators (unread / generating / errored) that drive the
 * conversation cards and the primary nav dot. Kept live by `chat.*` events on
 * /api/ui/stream; the runtime indicators are also set imperatively by the chat
 * stream hook so route changes do not clear them.
 */
import { createAsyncThunk, createSelector } from '@reduxjs/toolkit';
import { requestJson, withQuery } from '../http';

const emptyActive = () => ({
  threadId: null,
  detail: null,
  loading: false,
  error: null,
  requestId: null,
});

export const initialState = {
  chatRuntime: {
    unreadChatIds: [],
    generatingChatIds: [],
    erroredChatIds: [],
  },
  chats: {
    byId: {},
    order: [],
    loaded: false,
    loading: false,
    error: null,
    requestedAt: null,
    lastRequestTotal: null,
    active: emptyActive(),
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

function moveChatToFront(chats, threadId) {
  if (!threadId || !Array.isArray(chats.order) || !chats.order.includes(threadId)) {
    return;
  }
  chats.order = [threadId, ...chats.order.filter((item) => item !== threadId)];
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

export const fetchChatSessions = createAsyncThunk(
  'uiData/fetchChatSessions',
  ({ chatsUrl, includeArchived = true }) => requestJson(
    withQuery(chatsUrl, { include_archived: includeArchived ? 'true' : 'false' }),
    { errorLabel: 'Failed to load chats' },
  ),
);

function unwrapChatMutationSession(response) {
  if (response?.session && typeof response.session === 'object') {
    return response.session;
  }
  return response;
}

export const requestChatSessionDetail = createAsyncThunk(
  'uiData/requestChatSessionDetail',
  async ({ chatsUrl, threadId, requestId }) => {
    const session = await requestJson(
      withQuery(`${chatsUrl}/${encodeURIComponent(threadId)}`, { request_id: requestId }),
      { errorLabel: 'Failed to load chat' },
    );
    return { threadId, requestId, session };
  },
);

export const createChatSession = createAsyncThunk(
  'uiData/createChatSession',
  async ({ chatsUrl, title, threadId }) => unwrapChatMutationSession(await requestJson(chatsUrl, {
    method: 'POST',
    body: { title, thread_id: threadId },
    errorLabel: 'Failed to create chat card',
  })),
);

export const ensureDraftChatSession = createAsyncThunk(
  'uiData/ensureDraftChatSession',
  async ({ chatsUrl, threadId }) => unwrapChatMutationSession(await requestJson(
    `${chatsUrl.replace(/\/$/, '')}/draft`,
    {
      method: 'POST',
      body: { thread_id: threadId },
      errorLabel: 'Failed to open draft chat',
    },
  )),
);

export const updateChatSessionMetadata = createAsyncThunk(
  'uiData/updateChatSessionMetadata',
  async ({ chatsUrl, threadId, patch }) => unwrapChatMutationSession(await requestJson(
    `${chatsUrl}/${encodeURIComponent(threadId)}`,
    { method: 'PATCH', body: patch, errorLabel: 'Failed to update chat' },
  )),
);

export const deleteChatSession = createAsyncThunk(
  'uiData/deleteChatSession',
  async ({ chatsUrl, threadId }) => {
    await requestJson(`${chatsUrl}/${encodeURIComponent(threadId)}`, {
      method: 'DELETE',
      errorLabel: 'Failed to delete chat',
    });
    return { threadId };
  },
);

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
  'chat.sessions.snapshot': (state, payload) => {
    replaceChatList(state.chats, payload.sessions);
    state.chats.loaded = true;
    state.chats.loading = false;
    state.chats.error = null;
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
    if (
      payload.request_id
      && state.chats.active.requestId
      && payload.request_id !== state.chats.active.requestId
    ) {
      return;
    }
    state.chats.active.detail = payload.session || null;
    state.chats.active.threadId = payload.session?.thread_id || null;
    state.chats.active.loading = false;
    state.chats.active.error = null;
  },
  'chat.session.deleted': (state, payload) => {
    deleteChatFromList(state.chats, payload.thread_id);
    if (state.chats.active.threadId === payload.thread_id) {
      state.chats.active = emptyActive();
    }
  },
};

export function buildExtraReducers(builder) {
  builder
    .addCase(fetchChatSessions.pending, (state) => {
      state.chats.loading = true;
      state.chats.error = null;
    })
    .addCase(fetchChatSessions.fulfilled, (state, action) => {
      state.chats.loading = false;
      state.chats.requestedAt = new Date().toISOString();
      state.chats.lastRequestTotal = Number.isFinite(action.payload?.total)
        ? action.payload.total
        : state.chats.lastRequestTotal;
    })
    .addCase(fetchChatSessions.rejected, (state, action) => {
      state.chats.loading = false;
      state.chats.error = action.error?.message || 'Failed to load chats';
    })
    .addCase(requestChatSessionDetail.pending, (state, action) => {
      state.chats.active.threadId = action.meta.arg.threadId;
      state.chats.active.loading = true;
      state.chats.active.error = null;
      state.chats.active.requestId = action.meta.arg.requestId;
    })
    .addCase(requestChatSessionDetail.fulfilled, (state, action) => {
      if (state.chats.active.requestId === action.payload.requestId) {
        state.chats.active.loading = false;
        if (action.payload.session?.thread_id === action.payload.threadId) {
          upsertChat(state.chats, action.payload.session);
          state.chats.active.detail = action.payload.session;
          state.chats.active.threadId = action.payload.threadId;
          state.chats.active.error = null;
        }
      }
    })
    .addCase(requestChatSessionDetail.rejected, (state, action) => {
      if (state.chats.active.requestId === action.meta.arg.requestId) {
        state.chats.active.loading = false;
        state.chats.active.error = action.error?.message || 'Failed to load chat';
      }
    })
    .addCase(ensureDraftChatSession.fulfilled, (state, action) => {
      const threadId = action.payload?.thread_id;
      if (!threadId) {
        return;
      }
      upsertChat(state.chats, action.payload);
      moveChatToFront(state.chats, threadId);
      mergeActiveDetail(state.chats.active, action.payload);
    })
    .addCase(updateChatSessionMetadata.fulfilled, (state, action) => {
      const threadId = action.payload?.thread_id;
      if (!threadId) {
        return;
      }
      upsertChat(state.chats, action.payload);
      if (action.meta.arg?.patch?.touch === true) {
        moveChatToFront(state.chats, threadId);
      }
      mergeActiveDetail(state.chats.active, action.payload);
    })
    .addCase(deleteChatSession.fulfilled, (state, action) => {
      const threadId = action.payload?.threadId;
      deleteChatFromList(state.chats, threadId);
      if (state.chats.active.threadId === threadId) {
        state.chats.active = emptyActive();
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
export const selectErroredChatIds = (state) => state.uiData.chatRuntime.erroredChatIds;
export const selectChatIndicators = createSelector(
  [selectUnreadChatIds, selectGeneratingChatIds],
  (unreadChatIds, generatingChatIds) => ({
    hasUnread: unreadChatIds.length > 0,
    isGenerating: generatingChatIds.length > 0,
  }),
);
export const selectActiveChatSession = (state) => state.uiData.chats.active.detail;
export const selectActiveChatSessionState = (state) => state.uiData.chats.active;
