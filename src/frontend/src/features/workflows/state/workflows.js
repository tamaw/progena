import { UI_EVENT } from '../../../realtime/eventVocabulary';
/**
 * Workflows domain: backend-backed workflow definitions plus recent run state,
 * kept live by `workflows.snapshot`, `workflow.updated`, `workflow.deleted`,
 * and `workflow.run.*` events on /api/ui/stream.
 */
import { createSelector } from '@reduxjs/toolkit';
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle, addKeyedRequestLifecycle, completeKeyedRequest, selectKeyedRequest } from '../../../store/async';
import { withQuery } from '../../../store/http';
import { realtimeClientId, waitForRealtimeClient } from '../../../realtime/realtimeClient';


async function requestForWorkflow(url, options, requestId, query = {}) {
  await waitForRealtimeClient();
  return requestAcknowledged(
    withQuery(url, { ...query, client_id: realtimeClientId }),
    { ...options, requestId },
  );
}

function workflowPathForId(workflowId) {
  return workflowId ? `/workflows/${workflowId}.json` : '';
}

export const initialState = {
  workflows: {
    items: [],
    loaded: false,
    loading: false,
    error: null,
    requestId: null,
    detailByPath: {},
    detailRequestByPath: {},
    runs: [],
    runsLoaded: false,
    runsLoading: false,
    runsError: null,
    runsRequestId: null,
    runHistoryById: {},
    runHistoryRequestById: {},
  },
};

function applyWorkflowItems(state, payload) {
  state.workflows.items = Array.isArray(payload.items) ? payload.items : [];
  state.workflows.loaded = true;
  if (!state.workflows.loading || payload.request_id === state.workflows.requestId) {
    state.workflows.loading = false;
    state.workflows.error = null;
    state.workflows.requestId = null;
  }
}

function upsertWorkflowListItem(state, workflow) {
  if (!workflow?.id) {
    return;
  }
  const nodes = Object.values(workflow.graph?.nodes || {});
  const firstEvent = nodes.find((node) => node?.kind === 'event');
  // Same summary rule as the server's build_workflow_list_item.
  const triggers = nodes.flatMap((node) => {
    if (node?.kind === 'event') return [node.document_type || ''];
    if (node?.kind === 'cron') return [`cron ${node.expression || ''}`];
    if (node?.kind === 'manual') return ['manual'];
    return [];
  });
  const listItem = {
    id: workflow.id,
    title: workflow.title || 'Untitled workflow',
    description: workflow.description || '',
    enabled: workflow.enabled !== false,
    paused: Boolean(workflow.paused),
    version: Number.isFinite(workflow.version) ? workflow.version : 1,
    document_type: firstEvent?.document_type || '',
    triggers,
    node_count: nodes.length,
    updated_at: workflow.updated_at || '',
    created_at: workflow.created_at || '',
  };
  const index = state.workflows.items.findIndex((item) => item.id === workflow.id);
  if (index >= 0) {
    state.workflows.items[index] = listItem;
  } else {
    state.workflows.items.unshift(listItem);
  }
}

function upsertWorkflowDetail(state, payload) {
  if (!payload?.path || !payload?.workflow) {
    return;
  }
  completeKeyedRequest(state.workflows.detailRequestByPath, payload.path, payload.request_id);
  state.workflows.detailByPath[payload.path] = payload;
  upsertWorkflowListItem(state, payload.workflow);
}

function removeWorkflow(state, workflowId) {
  if (!workflowId) {
    return;
  }
  state.workflows.items = state.workflows.items.filter((item) => item.id !== workflowId);
  delete state.workflows.detailByPath[workflowPathForId(workflowId)];
  state.workflows.runs = state.workflows.runs.filter((run) => run.workflow_id !== workflowId);
  delete state.workflows.runHistoryById[workflowId];
  delete state.workflows.runHistoryRequestById[workflowId];
}

function sortRuns(runs) {
  runs.sort((left, right) => {
    const leftTime = Date.parse(left.updated_at || left.queued_at || '') || 0;
    const rightTime = Date.parse(right.updated_at || right.queued_at || '') || 0;
    if (leftTime !== rightTime) {
      return rightTime - leftTime;
    }
    return String(right.run_id || '').localeCompare(String(left.run_id || ''));
  });
}

// A window snapshot only knows recent runs, so a loaded history keeps its
// older rows and takes just the runs that are newer than its head.
function mergeRunIntoHistory(history, run) {
  const index = history.items.findIndex((item) => item.run_id === run.run_id);
  if (index >= 0) {
    history.items[index] = run;
    return;
  }
  const head = history.items[0];
  const runTime = Date.parse(run.updated_at || run.queued_at || '') || 0;
  const headTime = head ? Date.parse(head.updated_at || head.queued_at || '') || 0 : 0;
  if (!head || runTime >= headTime) {
    history.items.unshift(run);
    history.total += 1;
    sortRuns(history.items);
  }
}

function applyWorkflowRunHistory(state, payload) {
  const workflowId = payload.workflow_id;
  const incoming = Array.isArray(payload.items) ? payload.items : [];
  const offset = Number.isFinite(payload.offset) ? payload.offset : 0;
  const current = state.workflows.runHistoryById[workflowId];
  const kept = offset > 0 && current ? current.items.slice(0, offset) : [];
  const seen = new Set(kept.map((run) => run.run_id));
  state.workflows.runHistoryById[workflowId] = {
    items: [...kept, ...incoming.filter((run) => !seen.has(run.run_id))],
    total: Number.isFinite(payload.total) ? payload.total : incoming.length,
  };
  completeKeyedRequest(state.workflows.runHistoryRequestById, workflowId, payload.request_id);
}

function applyWorkflowRuns(state, payload) {
  if (payload.workflow_id) {
    applyWorkflowRunHistory(state, payload);
    return;
  }
  state.workflows.runs = Array.isArray(payload.items) ? [...payload.items] : [];
  sortRuns(state.workflows.runs);
  for (const run of state.workflows.runs) {
    const history = state.workflows.runHistoryById[run.workflow_id];
    if (history) mergeRunIntoHistory(history, run);
  }
  state.workflows.runsLoaded = true;
  if (!state.workflows.runsLoading || payload.request_id === state.workflows.runsRequestId) {
    state.workflows.runsLoading = false;
    state.workflows.runsError = null;
    state.workflows.runsRequestId = null;
  }
}

function upsertWorkflowRun(state, run) {
  if (!run?.run_id) {
    return;
  }
  const index = state.workflows.runs.findIndex((item) => item.run_id === run.run_id);
  if (index >= 0) {
    state.workflows.runs[index] = run;
  } else {
    state.workflows.runs.unshift(run);
  }
  sortRuns(state.workflows.runs);
  const history = state.workflows.runHistoryById[run.workflow_id];
  if (history) mergeRunIntoHistory(history, run);
}

export const fetchWorkflows = createAcknowledgedThunk('uiData/fetchWorkflows', async ({ workflowsUrl }, { requestId }) => (
  requestForWorkflow(workflowsUrl, {}, requestId)
));

export const fetchWorkflowContent = createAcknowledgedThunk('uiData/fetchWorkflowContent', async ({ workflowsUrl, path }, { requestId }) => (
  requestForWorkflow(`${workflowsUrl}/content`, {}, requestId, { path })
));

export const saveWorkflow = createAcknowledgedThunk('uiData/saveWorkflow', async ({ workflowsUrl, path, workflow }, { requestId }) => (
  requestForWorkflow(`${workflowsUrl}/content`, { method: 'PUT', body: { path, workflow } }, requestId)
));

export const createWorkflow = createAcknowledgedThunk('uiData/createWorkflow', async ({ workflowsUrl, title, description, documentType }, { requestId }) => (
  requestForWorkflow(workflowsUrl, {
    method: 'POST',
    body: { title, description, document_type: documentType },
  }, requestId)
));

export const deleteWorkflow = createAcknowledgedThunk('uiData/deleteWorkflow', async ({ workflowsUrl, workflowId }, { requestId }) => (
  requestForWorkflow(`${workflowsUrl}/${encodeURIComponent(workflowId)}`, { method: 'DELETE' }, requestId)
));

export const pauseWorkflow = createAcknowledgedThunk('uiData/pauseWorkflow', async ({ workflowsUrl, workflowId }, { requestId }) => (
  requestForWorkflow(`${workflowsUrl}/${encodeURIComponent(workflowId)}/pause`, { method: 'POST' }, requestId)
));

export const resumeWorkflow = createAcknowledgedThunk('uiData/resumeWorkflow', async ({ workflowsUrl, workflowId }, { requestId }) => (
  requestForWorkflow(`${workflowsUrl}/${encodeURIComponent(workflowId)}/resume`, { method: 'POST' }, requestId)
));

export const triggerWorkflow = createAcknowledgedThunk('uiData/triggerWorkflow', async ({ workflowsUrl, workflowId, payload }, { requestId }) => (
  requestForWorkflow(`${workflowsUrl}/${encodeURIComponent(workflowId)}/trigger`, {
    method: 'POST',
    body: payload ? { payload } : {},
  }, requestId)
));

export const fetchWorkflowRuns = createAcknowledgedThunk('uiData/fetchWorkflowRuns', async ({ workflowRunsUrl }, { requestId }) => (
  requestForWorkflow(workflowRunsUrl, {}, requestId)
));
// One page of a single workflow's history, newest first. Offset 0 starts over.
export const fetchWorkflowRunHistory = createAcknowledgedThunk('uiData/fetchWorkflowRunHistory', async ({ workflowRunsUrl, workflowId, offset = 0 }, { requestId }) => (
  requestForWorkflow(workflowRunsUrl, {}, requestId, { workflow_id: workflowId, offset })
));
export const answerWorkflowRunApproval = createAcknowledgedThunk('uiData/answerWorkflowRunApproval', async ({ workflowsUrl, workflowId, runId, approved }, { requestId }) => (
  requestForWorkflow(
    `${workflowsUrl}/${encodeURIComponent(workflowId)}/runs/${encodeURIComponent(runId)}/approval`,
    { method: 'POST', body: { approved } },
    requestId,
  )
));

export const eventHandlers = {
  [UI_EVENT.WORKFLOWS_SNAPSHOT]: (state, payload) => {
    applyWorkflowItems(state, payload);
  },
  [UI_EVENT.WORKFLOW_UPDATED]: (state, payload) => {
    upsertWorkflowDetail(state, payload);
  },
  [UI_EVENT.WORKFLOW_DELETED]: (state, payload) => {
    removeWorkflow(state, payload.workflow_id);
  },
  [UI_EVENT.WORKFLOW_RUNS_SNAPSHOT]: (state, payload) => {
    applyWorkflowRuns(state, payload);
  },
  [UI_EVENT.WORKFLOW_RUN_STARTED]: (state, payload) => {
    upsertWorkflowRun(state, payload);
  },
  [UI_EVENT.WORKFLOW_RUN_UPDATED]: (state, payload) => {
    upsertWorkflowRun(state, payload);
  },
  [UI_EVENT.WORKFLOW_RUN_COMPLETED]: (state, payload) => {
    upsertWorkflowRun(state, payload);
  },
  [UI_EVENT.WORKFLOW_RUN_FAILED]: (state, payload) => {
    upsertWorkflowRun(state, payload);
  },
};

export function buildExtraReducers(builder) {
  addRequestLifecycle(builder, fetchWorkflows, {
    select: (state) => state.workflows,
    errorMessage: 'Failed to load workflows',
  });
  addKeyedRequestLifecycle(builder, fetchWorkflowContent, {
    selectRequests: (state) => state.workflows.detailRequestByPath,
    keyFromArgs: ({ path } = {}) => path,
    errorMessage: 'Failed to load workflow detail',
  });
  addKeyedRequestLifecycle(builder, fetchWorkflowRunHistory, {
    selectRequests: (state) => state.workflows.runHistoryRequestById,
    keyFromArgs: ({ workflowId } = {}) => workflowId,
    errorMessage: 'Failed to load workflow run history',
  });
  return addRequestLifecycle(builder, fetchWorkflowRuns, {
    select: (state) => state.workflows,
    loadingKey: 'runsLoading',
    errorKey: 'runsError',
    requestIdKey: 'runsRequestId',
    errorMessage: 'Failed to load workflow runs',
  });
}

export const selectWorkflows = (state) => state.uiData.workflows.items;
export const selectWorkflowsState = (state) => state.uiData.workflows;
export const selectWorkflowRuns = (state) => state.uiData.workflows.runs;
export const selectWorkflowRunHistory = (state, workflowId) => (
  workflowId ? state.uiData.workflows.runHistoryById[workflowId] || null : null
);
export const selectWorkflowRunHistoryRequest = (state, workflowId) => (
  selectKeyedRequest(state.uiData.workflows.runHistoryRequestById, workflowId)
);
export const selectWorkflowDetailByPath = (state, path) => (
  path ? state.uiData.workflows.detailByPath[path] || null : null
);
export const selectWorkflowDetailRequest = (state, path) => (
  selectKeyedRequest(state.uiData.workflows.detailRequestByPath, path)
);
export const selectWorkflowLatestRunById = createSelector(
  [selectWorkflowRuns, (_state, workflowId) => workflowId],
  (runs, workflowId) => runs.find((run) => run.workflow_id === workflowId) || null,
);
