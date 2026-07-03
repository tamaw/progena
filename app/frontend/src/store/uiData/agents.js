/**
 * Agents domain: the agent roster plus the active (selected) agent detail and
 * graph, kept live by `agents.snapshot` / `agent.upserted` / `agent.selected` /
 * `agent.graph.updated` events on /api/ui/stream.
 */
import { createAsyncThunk, createSelector } from '@reduxjs/toolkit';
import { requestJson, withQuery } from '../http';

export const initialState = {
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
};

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

export const fetchAgentProfiles = createAsyncThunk(
  'uiData/fetchAgentProfiles',
  ({ agentsUrl }) => requestJson(agentsUrl, { errorLabel: 'Failed to load agents' }),
);

export const requestAgentDetail = createAsyncThunk(
  'uiData/requestAgentDetail',
  async ({ agentsUrl, agentId, requestId }) => {
    await requestJson(
      withQuery(`${agentsUrl}/${encodeURIComponent(agentId)}`, { request_id: requestId }),
      { errorLabel: 'Failed to load agent' },
    );
    return { agentId, requestId };
  },
);

export const updateAgentGraph = createAsyncThunk(
  'uiData/updateAgentGraph',
  ({ agentsUrl, agentId, nodes, edges, status, summary }) => requestJson(
    `${agentsUrl}/${encodeURIComponent(agentId)}/graph`,
    {
      method: 'PATCH',
      body: { nodes, edges, status, summary },
      errorLabel: 'Failed to update agent graph:',
    },
  ),
);

export const eventHandlers = {
  'agents.snapshot': (state, payload) => {
    replaceAgentList(state.agents, payload.agents);
    state.agents.loaded = true;
    state.agents.loading = false;
    state.agents.error = null;
  },
  'agent.upserted': (state, payload) => {
    upsertAgent(state.agents, payload.agent);
    state.agents.loaded = true;
  },
  'agent.selected': (state, payload) => {
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
  },
  'agent.graph.updated': (state, payload) => {
    const { agent_id: agentId, graph } = payload;
    if (agentId && state.agents.byId[agentId]) {
      state.agents.byId[agentId] = { ...state.agents.byId[agentId], graph };
    }
    if (agentId && state.agents.active.agentId === agentId && state.agents.active.detail) {
      state.agents.active.detail = { ...state.agents.active.detail, graph };
    }
  },
};

export function buildExtraReducers(builder) {
  builder
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
    });
}

export const selectAgentProfiles = createSelector(
  [(state) => state.uiData.agents.order, (state) => state.uiData.agents.byId],
  (order, byId) => order.map((agentId) => byId[agentId]).filter(Boolean),
);
export const selectAgentProfilesState = (state) => state.uiData.agents;
export const selectActiveAgentProfile = (state) => state.uiData.agents.active.detail;
export const selectActiveAgentProfileState = (state) => state.uiData.agents.active;
