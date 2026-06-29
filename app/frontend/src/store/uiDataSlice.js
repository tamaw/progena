import { createAsyncThunk, createSelector, createSlice } from '@reduxjs/toolkit';

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

export const fetchAgentProfiles = createAsyncThunk(
  'uiData/fetchAgentProfiles',
  async ({ agentsUrl }) => {
    const response = await fetch(agentsUrl, { headers: { Accept: 'application/json' } });
    if (!response.ok) {
      throw new Error(`Failed to load agents (${response.status})`);
    }
    return response.json();
  },
);

// Acknowledge a widget-list request; the snapshot arrives on the realtime
// stream as `widgets.updated` (the GET just triggers the broadcast).
export const fetchWidgets = createAsyncThunk(
  'uiData/fetchWidgets',
  async ({ widgetsUrl }) => {
    const response = await fetch(widgetsUrl, { headers: { Accept: 'application/json' } });
    if (!response.ok) {
      throw new Error(`Failed to load widgets (${response.status})`);
    }
    return response.json();
  },
);

// Acknowledge an MCP-list request; the snapshot arrives on the realtime stream
// as `mcp.snapshot` (the GET just triggers the broadcast).
export const fetchMcp = createAsyncThunk(
  'uiData/fetchMcp',
  async ({ mcpUrl }) => {
    const response = await fetch(mcpUrl, { headers: { Accept: 'application/json' } });
    if (!response.ok) {
      throw new Error(`Failed to load MCP servers (${response.status})`);
    }
    return response.json();
  },
);

// Acknowledge a knowledge-base list request; the snapshot arrives on the
// realtime stream as `knowledge.snapshot` (the GET also triggers the broadcast).
// The response carries the bases too, so the page populates even before the SSE
// frame lands.
export const fetchKnowledge = createAsyncThunk(
  'uiData/fetchKnowledge',
  async ({ knowledgeUrl }) => {
    const response = await fetch(knowledgeUrl, { headers: { Accept: 'application/json' } });
    if (!response.ok) {
      throw new Error(`Failed to load knowledge bases (${response.status})`);
    }
    return response.json();
  },
);

// Persist the full knowledge-base settings document. The backend validates it,
// saves it, and broadcasts `knowledge.snapshot`; the response also carries the
// snapshot so callers can read back the server-assigned ids.
export const saveKnowledge = createAsyncThunk(
  'uiData/saveKnowledge',
  async ({ knowledgeUrl, bases }) => {
    const response = await fetch(knowledgeUrl, {
      method: 'PUT',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify({ bases }),
    });
    if (!response.ok) {
      let detail = `(${response.status})`;
      try {
        const body = await response.json();
        if (body?.detail) {
          detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
        }
      } catch {
        // non-JSON error body; keep the status-code detail
      }
      throw new Error(`Failed to save knowledge bases: ${detail}`);
    }
    return response.json();
  },
);

// Walk + embed one folder source into SQL on the server, then update the doc.
export const indexKnowledgeSource = createAsyncThunk(
  'uiData/indexKnowledgeSource',
  async ({ knowledgeUrl, baseId, sourceId }) => {
    const response = await fetch(
      `${knowledgeUrl}/${encodeURIComponent(baseId)}/sources/${encodeURIComponent(sourceId)}/index`,
      { method: 'POST', headers: { Accept: 'application/json' } },
    );
    if (!response.ok) {
      throw new Error(`Failed to index source (${response.status})`);
    }
    return response.json();
  },
);

export const fetchKanbanBoard = createAsyncThunk(
  'uiData/fetchKanbanBoard',
  async ({ kanbanUrl }) => {
    const response = await fetch(kanbanUrl, { headers: { Accept: 'application/json' } });
    if (!response.ok) {
      throw new Error(`Failed to load tasks (${response.status})`);
    }
    return response.json();
  },
);

export const fetchTaskContent = createAsyncThunk(
  'uiData/fetchTaskContent',
  async ({ taskContentUrl, cardId }) => {
    const response = await fetch(`${taskContentUrl}/${encodeURIComponent(cardId)}/content`, {
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) {
      throw new Error(`Failed to load task brief (${response.status})`);
    }
    return response.json();
  },
);

export const fetchTaskActivity = createAsyncThunk(
  'uiData/fetchTaskActivity',
  async ({ taskActivityUrl, cardId }) => {
    const response = await fetch(`${taskActivityUrl}/${encodeURIComponent(cardId)}/activity`, {
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) {
      throw new Error(`Failed to load task activity (${response.status})`);
    }
    return response.json();
  },
);

export const requestAgentDetail = createAsyncThunk(
  'uiData/requestAgentDetail',
  async ({ agentsUrl, agentId, requestId }) => {
    const encodedId = encodeURIComponent(agentId);
    const separator = agentsUrl.includes('?') ? '&' : '?';
    const response = await fetch(
      `${agentsUrl}/${encodedId}${separator}request_id=${encodeURIComponent(requestId)}`,
      { headers: { Accept: 'application/json' } },
    );
    if (!response.ok) {
      throw new Error(`Failed to load agent (${response.status})`);
    }
    await response.json();
    return { agentId, requestId };
  },
);

export const updateAgentGraph = createAsyncThunk(
  'uiData/updateAgentGraph',
  async ({ agentsUrl, agentId, nodes, edges, status, summary }) => {
    const response = await fetch(`${agentsUrl}/${encodeURIComponent(agentId)}/graph`, {
      method: 'PATCH',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ nodes, edges, status, summary }),
    });
    if (!response.ok) {
      let detail = `(${response.status})`;
      try {
        const body = await response.json();
        if (body?.detail) {
          detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
        }
      } catch {
        // non-JSON error body; keep the status-code detail
      }
      throw new Error(`Failed to update agent graph: ${detail}`);
    }
    return response.json();
  },
);

const initialState = {
  status: null,
  alerts: null,
  widgets: [],
  // Schedule list, kept live by `schedule.snapshot` events on /api/ui/stream
  // and seeded once via REST when the Schedules page mounts.
  schedule: [],
  // Chat runtime state drives both the conversation cards and the primary nav
  // dot, so route changes do not clear unread/running indicators.
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
    active: {
      chatSessionId: null,
      detail: null,
      loading: false,
      error: null,
      requestId: null,
    },
  },
  agents: {
    byId: {},
    order: [],
    loaded: false,
    loading: false,
    error: null,
    active: {
      agentId: null,
      detail: null,
      loading: false,
      error: null,
      requestId: null,
    },
  },
  // MCP servers, kept live by `mcp.snapshot` events on /api/ui/stream and
  // seeded via REST when the Tools page mounts.
  mcp: {
    servers: [],
    loaded: false,
    loading: false,
    error: null,
  },
  tasks: {
    board: null,
    loaded: false,
    loading: false,
    error: null,
    contentById: {},
    activityById: {},
  },
  // Knowledge bases (settings), kept live by `knowledge.snapshot` events on
  // /api/ui/stream and seeded via REST when the Knowledge page mounts. Each base
  // is the raw API shape (snake_case) so edits can PUT the document back.
  knowledge: {
    bases: [],
    loaded: false,
    loading: false,
    error: null,
  },
};

function applyKnowledgeBases(state, bases) {
  state.knowledge.bases = Array.isArray(bases) ? bases : [];
  state.knowledge.loaded = true;
  state.knowledge.loading = false;
  state.knowledge.error = null;
}

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

function normalizeChatRuntimeIds(ids) {
  if (!Array.isArray(ids)) {
    return [];
  }
  return [...new Set(ids.filter((id) => typeof id === 'string' && id))];
}

function addChatRuntimeId(ids, chatSessionId) {
  if (typeof chatSessionId !== 'string' || !chatSessionId || ids.includes(chatSessionId)) {
    return ids;
  }
  return [...ids, chatSessionId];
}

function removeChatRuntimeId(ids, chatSessionId) {
  if (typeof chatSessionId !== 'string' || !chatSessionId || !ids.includes(chatSessionId)) {
    return ids;
  }
  return ids.filter((id) => id !== chatSessionId);
}

function replaceAgentList(agents, items) {
  agents.byId = {};
  agents.order = [];
  for (const agent of Array.isArray(items) ? items : []) {
    const agentId = typeof agent?.id === 'string' ? agent.id : null;
    if (!agentId || agents.byId[agentId]) {
      continue;
    }
    agents.byId[agentId] = agent;
    agents.order.push(agentId);
  }
}

function upsertAgent(agents, agent) {
  const agentId = typeof agent?.id === 'string' ? agent.id : null;
  if (!agentId) {
    return;
  }
  if (!agents.byId[agentId]) {
    agents.order.push(agentId);
  }
  agents.byId[agentId] = { ...(agents.byId[agentId] || {}), ...agent };
  if (agents.active.agentId === agentId && agents.active.detail) {
    agents.active.detail = { ...agents.active.detail, ...agent };
  }
}

const uiDataSlice = createSlice({
  name: 'uiData',
  initialState,
  reducers: {
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
      state.chats.active = {
        chatSessionId: null,
        detail: null,
        loading: false,
        error: null,
        requestId: null,
      };
    },
    scheduleSnapshotReceived(state, action) {
      state.schedule = Array.isArray(action.payload) ? action.payload : [];
    },
    kanbanSnapshotReceived(state, action) {
      state.tasks.board = action.payload || null;
      state.tasks.loaded = true;
      state.tasks.loading = false;
      state.tasks.error = null;
    },
    taskContentReceived(state, action) {
      const cardId = typeof action.payload?.card_id === 'string' ? action.payload.card_id : null;
      if (!cardId) {
        return;
      }
      state.tasks.contentById[cardId] = action.payload;
    },
    taskActivityReceived(state, action) {
      const cardId = typeof action.payload?.card_id === 'string' ? action.payload.card_id : null;
      if (!cardId) {
        return;
      }
      state.tasks.activityById[cardId] = action.payload;
    },
    applyUiEvent(state, action) {
      const event = action.payload;
      const payload = event?.payload || {};

      if (event?.type === 'connection.ready') {
        state.status = payload.status || state.status;
        state.alerts = payload.alerts || state.alerts;
        return;
      }
      if (event?.type === 'status.updated') {
        // Merge rather than replace so a partial push (e.g. the periodic
        // process-metrics tick) updates only the fields it carries and leaves
        // the rest (agents/tools/schedule/...) intact.
        state.status = { ...state.status, ...payload };
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
      if (event?.type === 'schedule.snapshot') {
        state.schedule = Array.isArray(payload.items) ? payload.items : [];
        return;
      }
      if (event?.type === 'mcp.snapshot') {
        state.mcp.servers = Array.isArray(payload.items) ? payload.items : [];
        state.mcp.loaded = true;
        state.mcp.loading = false;
        state.mcp.error = null;
        return;
      }
      if (event?.type === 'knowledge.snapshot') {
        applyKnowledgeBases(state, payload.bases);
        return;
      }
      if (event?.type === 'kanban.snapshot') {
        state.tasks.board = payload || null;
        state.tasks.loaded = true;
        state.tasks.loading = false;
        state.tasks.error = null;
        return;
      }
      if (event?.type === 'task.content.updated') {
        const cardId = typeof payload.card_id === 'string' ? payload.card_id : null;
        if (cardId) {
          state.tasks.contentById[cardId] = payload;
        }
        return;
      }
      if (event?.type === 'task.activity.updated') {
        const cardId = typeof payload.card_id === 'string' ? payload.card_id : null;
        if (cardId) {
          state.tasks.activityById[cardId] = payload;
        }
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
        return;
      }
      if (event?.type === 'agents.snapshot') {
        replaceAgentList(state.agents, payload.agents);
        state.agents.loaded = true;
        state.agents.loading = false;
        state.agents.error = null;
        return;
      }
      if (event?.type === 'agent.upserted') {
        upsertAgent(state.agents, payload.agent);
        state.agents.loaded = true;
        return;
      }
      if (event?.type === 'agent.selected') {
        if (
          payload.request_id
          && state.agents.active.requestId
          && payload.request_id !== state.agents.active.requestId
        ) {
          return;
        }
        state.agents.active.detail = payload.agent || null;
        state.agents.active.agentId = payload.agent?.id || null;
        state.agents.active.loading = false;
        state.agents.active.error = null;
        return;
      }
      if (event?.type === 'agent.graph.updated') {
        const { agent_id: agentId, graph } = payload;
        if (agentId && state.agents.byId[agentId]) {
          state.agents.byId[agentId] = { ...state.agents.byId[agentId], graph };
        }
        if (agentId && state.agents.active.agentId === agentId && state.agents.active.detail) {
          state.agents.active.detail = { ...state.agents.active.detail, graph };
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
      })
      .addCase(fetchAgentProfiles.pending, (state) => {
        state.agents.loading = true;
        state.agents.error = null;
      })
      .addCase(fetchAgentProfiles.fulfilled, (state) => {
        state.agents.loading = false;
      })
      .addCase(fetchAgentProfiles.rejected, (state, action) => {
        state.agents.loading = false;
        state.agents.error = action.error?.message || 'Failed to load agents';
      })
      .addCase(requestAgentDetail.pending, (state, action) => {
        state.agents.active.agentId = action.meta.arg.agentId;
        state.agents.active.loading = true;
        state.agents.active.error = null;
        state.agents.active.requestId = action.meta.arg.requestId;
      })
      .addCase(requestAgentDetail.fulfilled, (state, action) => {
        if (state.agents.active.requestId === action.payload.requestId) {
          state.agents.active.loading = false;
        }
      })
      .addCase(requestAgentDetail.rejected, (state, action) => {
        if (state.agents.active.requestId === action.meta.arg.requestId) {
          state.agents.active.loading = false;
          state.agents.active.error = action.error?.message || 'Failed to load agent';
        }
      })
      .addCase(fetchMcp.pending, (state) => {
        state.mcp.loading = true;
        state.mcp.error = null;
      })
      .addCase(fetchMcp.fulfilled, (state) => {
        state.mcp.loading = false;
      })
      .addCase(fetchMcp.rejected, (state, action) => {
        state.mcp.loading = false;
        state.mcp.error = action.error?.message || 'Failed to load MCP servers';
      })
      .addCase(fetchKnowledge.pending, (state) => {
        state.knowledge.loading = true;
        state.knowledge.error = null;
      })
      .addCase(fetchKnowledge.fulfilled, (state, action) => {
        applyKnowledgeBases(state, action.payload?.bases);
      })
      .addCase(fetchKnowledge.rejected, (state, action) => {
        state.knowledge.loading = false;
        state.knowledge.error = action.error?.message || 'Failed to load knowledge bases';
      })
      .addCase(saveKnowledge.fulfilled, (state, action) => {
        applyKnowledgeBases(state, action.payload?.bases);
      })
      .addCase(indexKnowledgeSource.fulfilled, (state, action) => {
        applyKnowledgeBases(state, action.payload?.bases);
      })
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
        const cardId = typeof action.payload?.card_id === 'string' ? action.payload.card_id : null;
        if (cardId) {
          state.tasks.contentById[cardId] = action.payload;
        }
      })
      .addCase(fetchTaskActivity.fulfilled, (state, action) => {
        const cardId = typeof action.payload?.card_id === 'string' ? action.payload.card_id : null;
        if (cardId) {
          state.tasks.activityById[cardId] = action.payload;
        }
      });
  },
});

export const {
  applyUiEvent,
  clearActiveChatSession,
  clearChatErrored,
  clearChatGenerating,
  clearChatUnread,
  kanbanSnapshotReceived,
  markChatErrored,
  markChatGenerating,
  markChatUnread,
  scheduleSnapshotReceived,
  setErroredChatIds,
  setGeneratingChatIds,
  taskActivityReceived,
  taskContentReceived,
  setUnreadChatIds,
} = uiDataSlice.actions;

export const selectUiStatus = (state) => state.uiData.status;
export const selectUiAlerts = (state) => state.uiData.alerts;
export const selectUiWidgets = (state) => state.uiData.widgets;
export const selectMcpServers = (state) => state.uiData.mcp.servers;
export const selectMcpState = (state) => state.uiData.mcp;
export const selectSchedules = (state) => state.uiData.schedule;
export const selectKnowledgeBases = (state) => state.uiData.knowledge.bases;
export const selectKnowledgeState = (state) => state.uiData.knowledge;
export const selectKanbanBoard = (state) => state.uiData.tasks.board;
export const selectTasksState = (state) => state.uiData.tasks;
export const selectTaskContentEntry = (state, cardId) => (
  cardId ? state.uiData.tasks.contentById[cardId] || null : null
);
export const selectTaskActivityEntry = (state, cardId) => (
  cardId ? state.uiData.tasks.activityById[cardId] || null : null
);
export const selectChatSessions = createSelector(
  [(state) => state.uiData.chats.order, (state) => state.uiData.chats.byId],
  (order, byId) => order.map((chatSessionId) => byId[chatSessionId]).filter(Boolean),
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
export const selectAgentProfiles = createSelector(
  [(state) => state.uiData.agents.order, (state) => state.uiData.agents.byId],
  (order, byId) => order.map((agentId) => byId[agentId]).filter(Boolean),
);
export const selectAgentProfilesState = (state) => state.uiData.agents;
export const selectActiveAgentProfile = (state) => state.uiData.agents.active.detail;
export const selectActiveAgentProfileState = (state) => state.uiData.agents.active;

export default uiDataSlice.reducer;
