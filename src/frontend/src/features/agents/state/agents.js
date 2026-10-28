/**
 * Agents domain: the agent roster plus the active (selected) agent detail and
 * graph, kept live by `agents.snapshot` / `agent.upserted` / `agent.selected` /
 * `agent.graph.updated` events on /api/ui/stream.
 */
import { createSelector } from '@reduxjs/toolkit';
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle } from '../../../store/async';

export const initialState = {
  agents: {
    byId: {},
    order: [],
    loaded: false,
    loading: false,
    error: null,
    requestId: null,
    deletedIds: {},
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
    // Checkpoint detail pushed to the viewed routine, cached by thread_id.
    // Historical snapshots are fetched once on demand.
    runStateByThreadId: {},
    runLifecycleByRequestId: {},
    lastRunNotification: null,
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
    delete agents.deletedIds[agentId];
    agents.order.push(agentId);
  }
  const activeId = agents.active.agentId;
  if (activeId && agents.active.detail && agents.byId[activeId]) {
    const detail = agents.active.detail;
    const listed = agents.byId[activeId];
    agents.active.detail = { ...detail, ...listed };
    // Roster snapshots intentionally omit routine graphs (they arrive on the
    // per-agent `agent.selected` detail event). A snapshot can race just after
    // that event on initial page load; don't let its `graph: null` erase the
    // graph already loaded for the active detail. Otherwise GraphCanvas stays
    // mounted with an empty fallback until the routine is selected again.
    if (listed.graph == null && detail.graph != null) {
      agents.active.detail.graph = detail.graph;
    }
  } else if (activeId && !agents.byId[activeId]) {
    agents.deletedIds[activeId] = true;
    agents.active = { agentId: null, detail: null, loading: false, error: null, requestId: null };
  }
}

function upsertAgent(agents, agent) {
  const agentId = typeof agent?.id === 'string' ? agent.id : null;
  if (!agentId) {
    return;
  }
  delete agents.deletedIds[agentId];
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

export const updateAgentGraph = createAcknowledgedThunk('uiData/updateAgentGraph', ({ agentsUrl, agentId, entry, nodes, edges, status, summary, policy_id, input_schema, output_schema, $defs }, { requestId }) => requestAcknowledged(
  `${agentsUrl}/${encodeURIComponent(agentId)}/graph`,
  {
    method: 'PATCH',
    body: { entry, nodes, edges, status, summary, policy_id, input_schema, output_schema, $defs },
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

export const startRoutineRun = createAcknowledgedThunk('uiData/startRoutineRun', ({ agentsUrl, agentId, sessionId, targetNode, parameters, resumeThreadId }, { requestId }) => requestAcknowledged(
  `${agentsUrl}/${encodeURIComponent(agentId)}/run`, {
    method: 'POST', requestId,
    body: {
      session_id: sessionId,
      ...(targetNode ? { target_node: targetNode } : {}),
      ...(resumeThreadId ? { resume_thread_id: resumeThreadId } : {}),
      ...(parameters !== undefined ? { parameters } : {}),
    },
  },
));

export const controlRoutineRun = createAcknowledgedThunk('uiData/controlRoutineRun', ({ agentsUrl, agentId, threadId, targetRequestId, action }, { requestId }) => requestAcknowledged(
  `${agentsUrl}/${encodeURIComponent(agentId)}/run-control`, {
    method: 'POST',
    requestId,
    body: { thread_id: threadId, request_id: targetRequestId, action },
  },
));

export const continueInterruptedRoutineRun = createAcknowledgedThunk('uiData/continueInterruptedRoutineRun', ({ agentsUrl, agentId, threadId, targetRequestId }, { requestId }) => requestAcknowledged(
  `${agentsUrl}/${encodeURIComponent(agentId)}/run-continue`, {
    method: 'POST',
    requestId,
    body: { thread_id: threadId, request_id: targetRequestId },
  },
));

export const answerRoutineApproval = createAcknowledgedThunk('uiData/answerRoutineApproval', ({ agentsUrl, agentId, threadId, interruptId, answer }, { requestId }) => requestAcknowledged(
  `${agentsUrl}/${encodeURIComponent(agentId)}/approval`, {
    method: 'POST', requestId, body: { thread_id: threadId, interrupt_id: interruptId, answer },
  },
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

const TERMINAL_RUN_STATUSES = new Set(['completed', 'failed', 'paused', 'stopped', 'cancelled']);
// "interrupted" is derived from a dead owner, not persisted; the attempt can
// still settle (Continue settles it stopped, Cancel run settles it cancelled).
const INTERRUPTED_EXITS = new Set(['interrupted', 'stopped', 'cancelled']);

function applyRunLifecycle(state, payload) {
  if (!payload?.request_id || !payload?.agent_id) return false;
  const previous = state.agents.runLifecycleByRequestId[payload.request_id];
  // Attempts never leave a terminal state. This guards against a delayed
  // control update disagreeing with the durable execution terminal event.
  if (previous && TERMINAL_RUN_STATUSES.has(previous.status) && previous.status !== payload.status) return false;
  if (previous?.status === 'interrupted' && !INTERRUPTED_EXITS.has(payload.status)) return false;
  if (previous && previous.timestamp > payload.timestamp) return false;
  state.agents.runLifecycleByRequestId[payload.request_id] = payload;
  return previous?.status !== payload.status;
}

export const eventHandlers = {
  ...Object.fromEntries(['enqueued', 'started', 'stopping', 'cancelling', 'paused', 'completed', 'failed', 'stopped', 'cancelled', 'interrupted'].map((status) => [
    `agent.routine.run.${status}`, (state, payload, event) => {
      const changed = applyRunLifecycle(state, { ...payload, status });
      if (changed && ['completed', 'failed'].includes(status)) {
        state.agents.lastRunNotification = { ...payload, status, seq: event.seq };
      }
    },
  ])),
  'agent.routine.control.updated': (state, payload) => {
    // payload.request_id is the control operation; the attempt is payload.run.
    if (payload?.run) applyRunLifecycle(state, payload.run);
  },
  'agent.routine.lifecycle.snapshot': (state, payload) => {
    for (const run of payload.runs || []) applyRunLifecycle(state, run);
  },
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
  'agent.deleted': (state, payload) => {
    const agentId = payload.agent_id;
    if (typeof agentId !== 'string') return;
    delete state.agents.byId[agentId];
    state.agents.order = state.agents.order.filter(id => id !== agentId);
    state.agents.deletedIds[agentId] = true;
    if (state.agents.active.agentId === agentId) {
      state.agents.active = { agentId: null, detail: null, loading: false, error: null, requestId: null };
    }
    // Runs and immutable revision caches remain available after live deletion.
  },
  'agent.selected': (state, payload) => {
    // Guard against a superseded *selection*, not a superseded request. More
    // than one request for the same agent is normal -- a stream reconnect
    // re-asks -- and keying this on request_id made each new ask invalidate
    // the previous one's answer, so a run of reconnects could reject every
    // reply in turn and leave the detail pane loading forever. What actually
    // must not land is another agent's detail after the selection moved on.
    const incoming = payload.agent?.id || null;
    if (incoming && state.agents.deletedIds[incoming]) return;
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
export const selectRoutineRunLifecycle = (state, requestId) => state.uiData.agents.runLifecycleByRequestId[requestId];
// One rule for "latest run" per routine: newest lifecycle timestamp wins.
const selectLatestRoutineRunByAgentId = createSelector(
  [(state) => state.uiData.agents.runLifecycleByRequestId],
  (runs) => {
    const latestByAgent = {};
    for (const run of Object.values(runs)) {
      if (!run.agent_id) continue;
      const existing = latestByAgent[run.agent_id];
      if (!existing || run.timestamp > existing.timestamp) {
        latestByAgent[run.agent_id] = run;
      }
    }
    return latestByAgent;
  },
);
const selectLatestRoutineRunByThreadId = createSelector(
  [(state) => state.uiData.agents.runLifecycleByRequestId],
  (runs) => {
    const latestByThread = {};
    for (const run of Object.values(runs)) {
      if (!run.thread_id) continue;
      const existing = latestByThread[run.thread_id];
      if (!existing || run.timestamp > existing.timestamp) {
        latestByThread[run.thread_id] = run;
      }
    }
    return latestByThread;
  },
);
export const selectLatestRoutineRunLifecycle = (state, agentId) => selectLatestRoutineRunByAgentId(state)[agentId] ?? null;
export const selectRoutineRunLifecyclesByThreadId = (state) => selectLatestRoutineRunByThreadId(state);
const RUN_STATUS_FILTER = {
  enqueued: 'Running',
  started: 'Running',
  stopping: 'Running',
  cancelling: 'Running',
  paused: 'Paused',
  stopped: 'Stopped',
  cancelled: 'Cancelled',
  interrupted: 'Interrupted',
  failed: 'Failed',
};
export const selectRoutineLatestRunStatusByAgentId = createSelector(
  [selectLatestRoutineRunByAgentId],
  (latestByAgent) => Object.fromEntries(Object.entries(latestByAgent)
    .map(([agentId, run]) => [agentId, RUN_STATUS_FILTER[run.status] ?? null])),
);
export const selectLastRoutineRunNotification = (state) => state.uiData.agents.lastRunNotification;
