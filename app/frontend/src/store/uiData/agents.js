/**
 * Agents domain: the agent roster plus the active (selected) agent detail and
 * graph, kept live by `agents.snapshot` / `agent.upserted` / `agent.selected` /
 * `agent.graph.updated` events on /api/ui/stream.
 */
import { createSelector } from '@reduxjs/toolkit';
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle } from './async';

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
    // SKILL.md content (front matter + body) for skills attached to an object
    // agent, fetched lazily on demand and cached by the manifest's absolute
    // `/skills/...` path (see `fetchAgentSkillContent` /
    // `agent.skill.content.updated`).
    skillsByPath: {},
    // Recent execution runs for a routine, fetched lazily when the Runs
    // side-panel opens and cached by agent id (see `fetchRoutineRuns` /
    // `agent.routine.runs.loaded`). `{ items, hasMore }` -- `items` newest
    // first, `hasMore` true when the backend's cursor-paginated `/runs`
    // route (LIMIT + `before`) has more pages; a `before`-bearing fetch
    // appends onto `items` instead of replacing it (see the reducer below).
    runsByAgentId: {},
    // Revision history for a routine, fetched lazily when the Revisions
    // side-panel opens and cached by agent id (see `fetchRoutineRevisions` /
    // `agent.routine.revisions.loaded`).
    revisionsByAgentId: {},
    // One revision's full graph document, fetched on click and cached by
    // `<agentId>:<revisionId>` (see `fetchRoutineRevision` /
    // `agent.routine.revision.loaded`).
    revisionContentByKey: {},
    // A run's live per-node timing/token/output state, polled while a run
    // is in flight and cached by thread_id (see `fetchRunState` /
    // `agent.routine.run_state.loaded`, and lib/routineRunPolling.js which
    // drives the poll loop).
    runStateByThreadId: {},
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
  const activeId = agents.active.agentId;
  if (activeId && agents.active.detail && agents.byId[activeId]) {
    agents.active.detail = { ...agents.active.detail, ...agents.byId[activeId] };
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

export const fetchAgentProfiles = createAcknowledgedThunk('uiData/fetchAgentProfiles', ({ agentsUrl }, { requestId }) => requestAcknowledged(agentsUrl, { requestId }));


export const requestAgentDetail = createAcknowledgedThunk('uiData/requestAgentDetail', async ({ agentsUrl, agentId }, { requestId }) => {
  return requestAcknowledged(
    `${agentsUrl}/${encodeURIComponent(agentId)}`,
    { requestId },
  );
});

export const updateAgentGraph = createAcknowledgedThunk('uiData/updateAgentGraph', ({ agentsUrl, agentId, entry, nodes, edges, status, summary, settings, input_schema, output_schema, $defs }, { requestId }) => requestAcknowledged(
  `${agentsUrl}/${encodeURIComponent(agentId)}/graph`,
  {
    method: 'PATCH',
    body: { entry, nodes, edges, status, summary, settings, input_schema, output_schema, $defs },
    requestId,
  },
));

export const deleteAgentProfile = createAcknowledgedThunk('uiData/deleteAgentProfile', ({ agentsUrl, agentId }, { requestId }) => requestAcknowledged(
  `${agentsUrl}/${encodeURIComponent(agentId)}`,
  { method: 'DELETE', requestId },
));

export const updateObjectAgentProfile = createAcknowledgedThunk('uiData/updateObjectAgentProfile', ({ agentsUrl, agentId, manifest }, { requestId }) => requestAcknowledged(
  `${agentsUrl}/object/${encodeURIComponent(agentId)}`,
  {
    method: 'PATCH',
    body: manifest,
    requestId,
  },
));

export const fetchAgentSkillContent = createAcknowledgedThunk('uiData/fetchAgentSkillContent', ({ agentsUrl, skillPath }, { requestId }) => requestAcknowledged(
  `${agentsUrl}/skills?path=${encodeURIComponent(skillPath)}`,
  { requestId },
));

export const fetchRoutineRuns = createAcknowledgedThunk('uiData/fetchRoutineRuns', ({ agentsUrl, agentId, before }, { requestId }) => requestAcknowledged(
  `${agentsUrl}/${encodeURIComponent(agentId)}/runs${before ? `?before=${encodeURIComponent(before)}` : ''}`,
  { requestId },
));

export const fetchRoutineRevisions = createAcknowledgedThunk('uiData/fetchRoutineRevisions', ({ agentsUrl, agentId }, { requestId }) => requestAcknowledged(
  `${agentsUrl}/${encodeURIComponent(agentId)}/revisions`,
  { requestId },
));

export const fetchRoutineRevision = createAcknowledgedThunk('uiData/fetchRoutineRevision', ({ agentsUrl, agentId, revisionId }, { requestId }) => requestAcknowledged(
  `${agentsUrl}/${encodeURIComponent(agentId)}/revisions/${encodeURIComponent(revisionId)}`,
  { requestId },
));

export const fetchRunState = createAcknowledgedThunk('uiData/fetchRunState', ({ agentsUrl, agentId, threadId }, { requestId }) => requestAcknowledged(
  `${agentsUrl}/${encodeURIComponent(agentId)}/run-state?thread_id=${encodeURIComponent(threadId)}`,
  { requestId },
));

export const updateAgentSkillContent = createAcknowledgedThunk('uiData/updateAgentSkillContent', ({ agentsUrl, skillPath, content }, { requestId }) => requestAcknowledged(
  `${agentsUrl}/skills?path=${encodeURIComponent(skillPath)}`,
  {
    method: 'PATCH',
    body: { content },
    requestId,
  },
));

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
  },
  'agent.selected': (state, payload) => {
    // Guard against a superseded *selection*, not a superseded request. More
    // than one request for the same agent is normal -- a stream reconnect
    // re-asks -- and keying this on request_id made each new ask invalidate
    // the previous one's answer, so a run of reconnects could reject every
    // reply in turn and leave the detail pane loading forever. What actually
    // must not land is another agent's detail after the selection moved on.
    const incoming = payload.agent?.id || null;
    if (incoming) {
      if (state.agents.active.agentId && incoming !== state.agents.active.agentId) return;
    } else if (state.agents.active.requestId && payload.request_id !== state.agents.active.requestId) {
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
  'agent.skill.content.updated': (state, payload) => {
    if (typeof payload?.path === 'string' && payload.path) {
      state.agents.skillsByPath[payload.path] = payload;
    }
  },
  'agent.routine.runs.loaded': (state, payload) => {
    if (typeof payload?.agent_id !== 'string' || !payload.agent_id) return;
    const runs = Array.isArray(payload.runs) ? payload.runs : [];
    const hasMore = Boolean(payload.has_more);
    // A `before`-bearing response is a "Load more" page -- append past the
    // existing tail (deduped by thread_id, in case a slow page race lands
    // twice) rather than replacing, which would otherwise drop everything
    // already scrolled past. No `before` means a fresh/initial load: the
    // list may have moved on since we last looked, so replace outright.
    if (payload.before) {
      const existing = state.agents.runsByAgentId[payload.agent_id]?.items || [];
      const existingIds = new Set(existing.map((run) => run.thread_id));
      const appended = runs.filter((run) => !existingIds.has(run.thread_id));
      state.agents.runsByAgentId[payload.agent_id] = { items: [...existing, ...appended], hasMore };
    } else {
      state.agents.runsByAgentId[payload.agent_id] = { items: runs, hasMore };
    }
  },
  'agent.routine.revisions.loaded': (state, payload) => {
    if (typeof payload?.agent_id === 'string' && payload.agent_id) {
      state.agents.revisionsByAgentId[payload.agent_id] = Array.isArray(payload.revisions) ? payload.revisions : [];
    }
  },
  'agent.routine.revision.loaded': (state, payload) => {
    if (typeof payload?.agent_id === 'string' && payload.agent_id && typeof payload?.revision_id === 'string' && payload.revision_id) {
      state.agents.revisionContentByKey[`${payload.agent_id}:${payload.revision_id}`] = payload.graph;
    }
  },
  'agent.routine.run_state.loaded': (state, payload) => {
    if (typeof payload?.thread_id === 'string' && payload.thread_id) {
      state.agents.runStateByThreadId[payload.thread_id] = payload;
    }
  },
};

export function buildExtraReducers(builder) {
  addRequestLifecycle(builder, fetchAgentProfiles, {
    select: (state) => state.agents,
    errorMessage: 'Failed to load agents',
  });
  return addRequestLifecycle(builder, requestAgentDetail, {
    select: (state) => state.agents.active,
    errorMessage: 'Failed to load agent',
    onPending: (state, action) => {
      state.agents.active.agentId = action.meta.arg.agentId;
    },
  });
}

export const selectAgentProfiles = createSelector(
  [(state) => state.uiData.agents.order, (state) => state.uiData.agents.byId],
  (order, byId) => order.map((agentId) => byId[agentId]).filter(Boolean),
);
export const selectAgentProfilesState = (state) => state.uiData.agents;
export const selectActiveAgentProfile = (state) => state.uiData.agents.active.detail;
export const selectActiveAgentProfileState = (state) => state.uiData.agents.active;
export const selectAgentSkillContent = (state, skillPath) => state.uiData.agents.skillsByPath[skillPath];
export const selectAgentRoutineRuns = (state, agentId) => state.uiData.agents.runsByAgentId[agentId]?.items;
export const selectAgentRoutineRunsHasMore = (state, agentId) => Boolean(state.uiData.agents.runsByAgentId[agentId]?.hasMore);
export const selectAgentRoutineRevisions = (state, agentId) => state.uiData.agents.revisionsByAgentId[agentId];
export const selectAgentRoutineRevisionContent = (state, agentId, revisionId) => state.uiData.agents.revisionContentByKey[`${agentId}:${revisionId}`];
export const selectAgentRunState = (state, threadId) => (threadId ? state.uiData.agents.runStateByThreadId[threadId] : undefined);
