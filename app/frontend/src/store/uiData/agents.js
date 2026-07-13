/**
 * Agents domain: the agent roster plus the active (selected) agent detail and
 * graph, kept live by `agents.snapshot` / `agent.upserted` / `agent.selected` /
 * `agent.graph.updated` events on /api/ui/stream.
 */
import { createAsyncThunk, createSelector } from '@reduxjs/toolkit';
import { createOperationRequestId, requestAcknowledged } from '../http';

export const initialState = {
  agents: {
    byId: {},
    order: [],
    loaded: false,
    loading: false,
    error: null,
    requestId: null,
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

export const fetchAgentProfiles = createAsyncThunk('uiData/fetchAgentProfiles', ({ agentsUrl }, { requestId }) => requestAcknowledged(agentsUrl, { requestId }), { idGenerator: createOperationRequestId });

export const requestAgentDetail = createAsyncThunk('uiData/requestAgentDetail', async ({ agentsUrl, agentId }, { requestId }) => {
  return requestAcknowledged(
    `${agentsUrl}/${encodeURIComponent(agentId)}`,
    { requestId },
  );
}, { idGenerator: createOperationRequestId });

export const updateAgentGraph = createAsyncThunk('uiData/updateAgentGraph', ({ agentsUrl, agentId, nodes, edges, status, summary }, { requestId }) => requestAcknowledged(
  `${agentsUrl}/${encodeURIComponent(agentId)}/graph`,
  {
    method: 'PATCH',
    body: { nodes, edges, status, summary },
    requestId,
  },
), { idGenerator: createOperationRequestId });

export const eventHandlers = {
  'agents.snapshot': (state, payload) => {
    replaceAgentList(state.agents, payload.agents);
    state.agents.loaded = true;
    if (!state.agents.loading || payload.request_id === state.agents.requestId) {
      state.agents.loading = false;
      state.agents.error = null;
      state.agents.requestId = null;
    }
  },
  'agent.upserted': (state, payload) => {
    upsertAgent(state.agents, payload.agent);
    state.agents.loaded = true;
  },
  'agent.selected': (state, payload) => {
    if (state.agents.active.requestId && payload.request_id !== state.agents.active.requestId) {
      return;
    }
    state.agents.active.detail = payload.agent || null;
    state.agents.active.agentId = payload.agent?.id || null;
    state.agents.active.loading = false;
    state.agents.active.error = null;
    state.agents.active.requestId = null;
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
    .addCase(fetchAgentProfiles.pending, (state, action) => {
      state.agents.loading = true;
      state.agents.error = null;
      state.agents.requestId = action.meta.requestId;
    })
    .addCase(fetchAgentProfiles.fulfilled, (state) => {
      state.agents.loading = false;
      state.agents.loaded = true;
    })
    .addCase(fetchAgentProfiles.rejected, (state, action) => {
      if (state.agents.requestId !== action.meta.requestId) {
        return;
      }
      state.agents.loading = false;
      state.agents.loaded = true;
      state.agents.error = action.error?.message || 'Failed to load agents';
      state.agents.requestId = null;
    })
    .addCase(requestAgentDetail.pending, (state, action) => {
      state.agents.active.agentId = action.meta.arg.agentId;
      state.agents.active.loading = true;
      state.agents.active.error = null;
      state.agents.active.requestId = action.meta.requestId;
    })
    .addCase(requestAgentDetail.rejected, (state, action) => {
      if (state.agents.active.requestId === action.meta.requestId) {
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
