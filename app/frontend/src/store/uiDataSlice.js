import { createAsyncThunk, createSlice } from '@reduxjs/toolkit';

export const fetchChatSessions = createAsyncThunk(
  'uiData/fetchChatSessions',
  async ({ chatsUrl, includeArchived = true }) => {
    const separator = chatsUrl.includes('?') ? '&' : '?';
    const response = await fetch(`${chatsUrl}${separator}include_archived=${includeArchived ? 'true' : 'false'}`, {
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) {
      throw new Error(`Failed to load chats (${response.status})`);
    }
    return response.json();
  },
);

export const requestChatSessionDetail = createAsyncThunk(
  'uiData/requestChatSessionDetail',
  async ({ chatsUrl, chatSessionId, requestId }) => {
    const encodedChatId = encodeURIComponent(chatSessionId);
    const separator = chatsUrl.includes('?') ? '&' : '?';
    const response = await fetch(
      `${chatsUrl}/${encodedChatId}${separator}request_id=${encodeURIComponent(requestId)}`,
      { headers: { Accept: 'application/json' } },
    );
    if (!response.ok) {
      throw new Error(`Failed to load chat (${response.status})`);
    }
    await response.json();
    return { chatSessionId, requestId };
  },
);

export const createChatSession = createAsyncThunk(
  'uiData/createChatSession',
  async ({ chatsUrl, title, progenitorSessionId }) => {
    const response = await fetch(chatsUrl, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        title,
        progenitor_session_id: progenitorSessionId,
      }),
    });
    if (!response.ok) {
      throw new Error(`Failed to create chat card (${response.status})`);
    }
    return response.json();
  },
);

export const updateChatSessionMetadata = createAsyncThunk(
  'uiData/updateChatSessionMetadata',
  async ({ chatsUrl, chatSessionId, patch }) => {
    const response = await fetch(`${chatsUrl}/${encodeURIComponent(chatSessionId)}`, {
      method: 'PATCH',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(patch),
    });
    if (!response.ok) {
      throw new Error(`Failed to update chat (${response.status})`);
    }
    return response.json();
  },
);

export const deleteChatSession = createAsyncThunk(
  'uiData/deleteChatSession',
  async ({ chatsUrl, chatSessionId }) => {
    const response = await fetch(`${chatsUrl}/${encodeURIComponent(chatSessionId)}`, {
      method: 'DELETE',
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) {
      throw new Error(`Failed to delete chat (${response.status})`);
    }
    return { chatSessionId };
  },
);

const initialState = {
  status: null,
  alerts: null,
  widgets: [],
  chats: {
    byId: {},
    order: [],
    loaded: false,
    loading: false,
    error: null,
    requestedAt: null,
    lastRequestTotal: null,
    active: {
      chatSessionId: null,
      detail: null,
      loading: false,
      error: null,
      requestId: null,
    },
  },
};

function sessionIdFor(session) {
  return typeof session?.chat_session_id === 'string' && session.chat_session_id
    ? session.chat_session_id
    : null;
}

function replaceChatList(chats, sessions) {
  chats.byId = {};
  chats.order = [];
  for (const session of Array.isArray(sessions) ? sessions : []) {
    const chatSessionId = sessionIdFor(session);
    if (!chatSessionId || chats.byId[chatSessionId]) {
      continue;
    }
    chats.byId[chatSessionId] = session;
    chats.order.push(chatSessionId);
  }
}

function upsertChat(chats, session) {
  const chatSessionId = sessionIdFor(session);
  if (!chatSessionId) {
    return;
  }
  if (!chats.byId[chatSessionId]) {
    chats.order.unshift(chatSessionId);
  }
  chats.byId[chatSessionId] = {
    ...(chats.byId[chatSessionId] || {}),
    ...session,
  };
}

function addChatIfMissing(chats, session) {
  const chatSessionId = sessionIdFor(session);
  if (!chatSessionId || chats.byId[chatSessionId]) {
    return;
  }
  chats.byId[chatSessionId] = session;
  chats.order.unshift(chatSessionId);
}

function deleteChatFromList(chats, chatSessionId) {
  if (!chatSessionId) {
    return;
  }
  delete chats.byId[chatSessionId];
  chats.order = chats.order.filter((item) => item !== chatSessionId);
}

function mergeActiveDetail(active, session) {
  if (!session?.chat_session_id) {
    return;
  }
  if (active.detail?.chat_session_id !== session.chat_session_id) {
    return;
  }
  active.detail = {
    ...active.detail,
    ...session,
    messages: active.detail.messages || [],
  };
}

const uiDataSlice = createSlice({
  name: 'uiData',
  initialState,
  reducers: {
    applyUiEvent(state, action) {
      const event = action.payload;
      const payload = event?.payload || {};

      if (event?.type === 'connection.ready') {
        state.status = payload.status || state.status;
        state.alerts = payload.alerts || state.alerts;
        return;
      }
      if (event?.type === 'status.updated') {
        state.status = payload;
        if (payload.alerts) {
          state.alerts = payload.alerts;
        }
        if (Array.isArray(payload.widgets)) {
          state.widgets = payload.widgets;
        }
        return;
      }
      if (event?.type === 'alerts.updated') {
        state.alerts = payload;
        return;
      }
      if (event?.type === 'widgets.updated') {
        state.widgets = Array.isArray(payload.items) ? payload.items : [];
        return;
      }
      if (event?.type === 'chat.sessions.snapshot') {
        replaceChatList(state.chats, payload.sessions);
        state.chats.loaded = true;
        state.chats.loading = false;
        state.chats.error = null;
        return;
      }
      if (
        event?.type === 'chat.session.created'
        || event?.type === 'chat.session.metadata.updated'
      ) {
        upsertChat(state.chats, payload.session);
        mergeActiveDetail(state.chats.active, payload.session);
        state.chats.loaded = true;
        return;
      }
      if (event?.type === 'chat.session.upserted') {
        addChatIfMissing(state.chats, payload.session);
        state.chats.loaded = true;
        return;
      }
      if (event?.type === 'chat.session.selected') {
        if (
          payload.request_id
          && state.chats.active.requestId
          && payload.request_id !== state.chats.active.requestId
        ) {
          return;
        }
        state.chats.active.detail = payload.session || null;
        state.chats.active.chatSessionId = payload.session?.chat_session_id || null;
        state.chats.active.loading = false;
        state.chats.active.error = null;
        return;
      }
      if (event?.type === 'chat.session.deleted') {
        deleteChatFromList(state.chats, payload.chat_session_id);
        if (state.chats.active.chatSessionId === payload.chat_session_id) {
          state.chats.active = {
            chatSessionId: null,
            detail: null,
            loading: false,
            error: null,
            requestId: null,
          };
        }
      }
    },
  },
  extraReducers: (builder) => {
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
        state.chats.active.chatSessionId = action.meta.arg.chatSessionId;
        state.chats.active.loading = true;
        state.chats.active.error = null;
        state.chats.active.requestId = action.meta.arg.requestId;
      })
      .addCase(requestChatSessionDetail.fulfilled, (state, action) => {
        if (state.chats.active.requestId === action.payload.requestId) {
          state.chats.active.loading = false;
        }
      })
      .addCase(requestChatSessionDetail.rejected, (state, action) => {
        if (state.chats.active.requestId === action.meta.arg.requestId) {
          state.chats.active.loading = false;
          state.chats.active.error = action.error?.message || 'Failed to load chat';
        }
      });
  },
});

export const { applyUiEvent } = uiDataSlice.actions;

export const selectUiStatus = (state) => state.uiData.status;
export const selectUiAlerts = (state) => state.uiData.alerts;
export const selectUiWidgets = (state) => state.uiData.widgets;
export const selectChatSessions = (state) =>
  state.uiData.chats.order
    .map((chatSessionId) => state.uiData.chats.byId[chatSessionId])
    .filter(Boolean);
export const selectChatSessionsState = (state) => state.uiData.chats;
export const selectActiveChatSession = (state) => state.uiData.chats.active.detail;
export const selectActiveChatSessionState = (state) => state.uiData.chats.active;

export default uiDataSlice.reducer;
