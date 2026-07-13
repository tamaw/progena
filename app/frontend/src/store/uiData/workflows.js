/**
 * Workflows domain: backend-backed workflow definitions plus recent run state,
 * kept live by `workflows.snapshot`, `workflow.updated`, `workflow.deleted`,
 * and `workflow.run.*` events on /api/ui/stream.
 */
import { createAsyncThunk, createSelector } from '@reduxjs/toolkit';
import { createOperationRequestId, requestAcknowledged, withQuery } from '../http';

function workflowPathForId(workflowId) {
  return workflowId ? `/state/workflows/${workflowId}.json` : '';
}

export const initialState = {
  workflows: {
    items: [],
    loaded: false,
    loading: false,
    error: null,
    requestId: null,
    detailByPath: {},
    runs: [],
    runsLoaded: false,
    runsLoading: false,
    runsError: null,
    runsRequestId: null,
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
  const listItem = {
    id: workflow.id,
    title: workflow.title || 'Untitled workflow',
    description: workflow.description || '',
    enabled: workflow.enabled !== false,
    paused: Boolean(workflow.paused),
    version: Number.isFinite(workflow.version) ? workflow.version : 1,
    event_type: workflow.trigger?.event_type || '',
    node_count: Object.keys(workflow.graph?.nodes || {}).length,
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

function applyWorkflowRuns(state, payload) {
  state.workflows.runs = Array.isArray(payload.items) ? [...payload.items] : [];
  sortRuns(state.workflows.runs);
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
}

export const fetchWorkflows = createAsyncThunk('uiData/fetchWorkflows', async ({ workflowsUrl }, { requestId }) => {
  return requestAcknowledged(workflowsUrl, { requestId });
}, { idGenerator: createOperationRequestId });

export const fetchWorkflowContent = createAsyncThunk('uiData/fetchWorkflowContent', async ({ workflowsUrl, path }, { requestId }) => {
  return requestAcknowledged(
    withQuery(`${workflowsUrl}/content`, { path }),
    { requestId },
  );
}, { idGenerator: createOperationRequestId });

export const saveWorkflow = createAsyncThunk('uiData/saveWorkflow', async ({ workflowsUrl, path, workflow }, { requestId }) => {
  return requestAcknowledged(`${workflowsUrl}/content`, {
    method: 'PUT',
    body: { path, workflow },
    requestId,
  });
}, { idGenerator: createOperationRequestId });

export const createWorkflow = createAsyncThunk('uiData/createWorkflow', async ({ workflowsUrl, title, description, eventType }, { requestId }) => {
  return requestAcknowledged(workflowsUrl, {
    method: 'POST',
    body: {
      title,
      description,
      event_type: eventType,
    },
    requestId,
  });
}, { idGenerator: createOperationRequestId });

export const deleteWorkflow = createAsyncThunk('uiData/deleteWorkflow', async ({ workflowsUrl, workflowId }, { requestId }) => {
  return requestAcknowledged(
    `${workflowsUrl}/${encodeURIComponent(workflowId)}`,
    { method: 'DELETE', requestId },
  );
}, { idGenerator: createOperationRequestId });

export const pauseWorkflow = createAsyncThunk('uiData/pauseWorkflow', async ({ workflowsUrl, workflowId }, { requestId }) => {
  return requestAcknowledged(
    `${workflowsUrl}/${encodeURIComponent(workflowId)}/pause`,
    { method: 'POST', requestId },
  );
}, { idGenerator: createOperationRequestId });

export const resumeWorkflow = createAsyncThunk('uiData/resumeWorkflow', async ({ workflowsUrl, workflowId }, { requestId }) => {
  return requestAcknowledged(
    `${workflowsUrl}/${encodeURIComponent(workflowId)}/resume`,
    { method: 'POST', requestId },
  );
}, { idGenerator: createOperationRequestId });

export const triggerWorkflow = createAsyncThunk('uiData/triggerWorkflow', async ({ workflowsUrl, workflowId, eventType, payload }, { requestId }) => {
  return requestAcknowledged(
    `${workflowsUrl}/${encodeURIComponent(workflowId)}/trigger`,
    {
      method: 'POST',
      body: {
        ...(eventType ? { event_type: eventType } : {}),
        ...(payload ? { payload } : {}),
      },
      requestId,
    },
  );
}, { idGenerator: createOperationRequestId });

export const fetchWorkflowRuns = createAsyncThunk('uiData/fetchWorkflowRuns', async ({ workflowRunsUrl }, { requestId }) => {
  return requestAcknowledged(workflowRunsUrl, { requestId });
}, { idGenerator: createOperationRequestId });

export const eventHandlers = {
  'workflows.snapshot': (state, payload) => {
    applyWorkflowItems(state, payload);
  },
  'workflow.updated': (state, payload) => {
    upsertWorkflowDetail(state, payload);
  },
  'workflow.deleted': (state, payload) => {
    removeWorkflow(state, payload.workflow_id);
  },
  'workflow.runs.snapshot': (state, payload) => {
    applyWorkflowRuns(state, payload);
  },
  'workflow.run.started': (state, payload) => {
    upsertWorkflowRun(state, payload);
  },
  'workflow.run.updated': (state, payload) => {
    upsertWorkflowRun(state, payload);
  },
  'workflow.run.completed': (state, payload) => {
    upsertWorkflowRun(state, payload);
  },
  'workflow.run.failed': (state, payload) => {
    upsertWorkflowRun(state, payload);
  },
};

export function buildExtraReducers(builder) {
  builder
    .addCase(fetchWorkflows.pending, (state, action) => {
      state.workflows.loading = true;
      state.workflows.error = null;
      state.workflows.requestId = action.meta.requestId;
    })
    .addCase(fetchWorkflows.rejected, (state, action) => {
      if (state.workflows.requestId !== action.meta.requestId) {
        return;
      }
      state.workflows.loading = false;
      state.workflows.error = action.error?.message || 'Failed to load workflows';
      state.workflows.requestId = null;
    })
    .addCase(fetchWorkflowRuns.pending, (state, action) => {
      state.workflows.runsLoading = true;
      state.workflows.runsError = null;
      state.workflows.runsRequestId = action.meta.requestId;
    })
    .addCase(fetchWorkflowRuns.rejected, (state, action) => {
      if (state.workflows.runsRequestId !== action.meta.requestId) {
        return;
      }
      state.workflows.runsLoading = false;
      state.workflows.runsError = action.error?.message || 'Failed to load workflow runs';
      state.workflows.runsRequestId = null;
    });
}

export const selectWorkflows = (state) => state.uiData.workflows.items;
export const selectWorkflowsState = (state) => state.uiData.workflows;
export const selectWorkflowRuns = (state) => state.uiData.workflows.runs;
export const selectWorkflowDetailByPath = (state, path) => (
  path ? state.uiData.workflows.detailByPath[path] || null : null
);
export const selectWorkflowLatestRunById = createSelector(
  [selectWorkflowRuns, (_state, workflowId) => workflowId],
  (runs, workflowId) => runs.find((run) => run.workflow_id === workflowId) || null,
);
