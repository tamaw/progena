/**
 * Workflows domain: backend-backed workflow definitions plus recent run state,
 * kept live by `workflows.snapshot`, `workflow.updated`, `workflow.deleted`,
 * and `workflow.run.*` events on /api/ui/stream.
 */
import { createAsyncThunk, createSelector } from '@reduxjs/toolkit';
import { requestJson, withQuery } from '../http';

function workflowPathForId(workflowId) {
  return workflowId ? `/state/workflows/${workflowId}.json` : '';
}

export const initialState = {
  workflows: {
    items: [],
    loaded: false,
    loading: false,
    error: null,
    detailByPath: {},
    runs: [],
    runsLoaded: false,
    runsLoading: false,
    runsError: null,
  },
};

function applyWorkflowItems(state, items) {
  state.workflows.items = Array.isArray(items) ? items : [];
  state.workflows.loaded = true;
  state.workflows.loading = false;
  state.workflows.error = null;
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

function applyWorkflowRuns(state, items) {
  state.workflows.runs = Array.isArray(items) ? [...items] : [];
  sortRuns(state.workflows.runs);
  state.workflows.runsLoaded = true;
  state.workflows.runsLoading = false;
  state.workflows.runsError = null;
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

export const fetchWorkflows = createAsyncThunk(
  'uiData/fetchWorkflows',
  ({ workflowsUrl }) => requestJson(workflowsUrl, { errorLabel: 'Failed to load workflows' }),
);

export const fetchWorkflowContent = createAsyncThunk(
  'uiData/fetchWorkflowContent',
  ({ workflowsUrl, path }) => requestJson(
    withQuery(`${workflowsUrl}/content`, { path }),
    { errorLabel: 'Failed to load workflow' },
  ),
);

export const saveWorkflow = createAsyncThunk(
  'uiData/saveWorkflow',
  ({ workflowsUrl, path, workflow }) => requestJson(`${workflowsUrl}/content`, {
    method: 'PUT',
    body: { path, workflow },
    errorLabel: 'Failed to save workflow',
  }),
);

export const createWorkflow = createAsyncThunk(
  'uiData/createWorkflow',
  ({ workflowsUrl, title, description, eventType }) => requestJson(workflowsUrl, {
    method: 'POST',
    body: {
      title,
      description,
      event_type: eventType,
    },
    errorLabel: 'Failed to create workflow',
  }),
);

export const deleteWorkflow = createAsyncThunk(
  'uiData/deleteWorkflow',
  async ({ workflowsUrl, workflowId }) => {
    await requestJson(`${workflowsUrl}/${encodeURIComponent(workflowId)}`, {
      method: 'DELETE',
      errorLabel: 'Failed to delete workflow',
    });
    return { workflowId };
  },
);

export const pauseWorkflow = createAsyncThunk(
  'uiData/pauseWorkflow',
  ({ workflowsUrl, workflowId }) => requestJson(`${workflowsUrl}/${encodeURIComponent(workflowId)}/pause`, {
    method: 'POST',
    errorLabel: 'Failed to pause workflow',
  }),
);

export const resumeWorkflow = createAsyncThunk(
  'uiData/resumeWorkflow',
  ({ workflowsUrl, workflowId }) => requestJson(`${workflowsUrl}/${encodeURIComponent(workflowId)}/resume`, {
    method: 'POST',
    errorLabel: 'Failed to resume workflow',
  }),
);

export const triggerWorkflow = createAsyncThunk(
  'uiData/triggerWorkflow',
  ({ workflowsUrl, workflowId, eventType, payload }) => requestJson(`${workflowsUrl}/${encodeURIComponent(workflowId)}/trigger`, {
    method: 'POST',
    body: {
      ...(eventType ? { event_type: eventType } : {}),
      ...(payload ? { payload } : {}),
    },
    errorLabel: 'Failed to trigger workflow',
  }),
);

export const fetchWorkflowRuns = createAsyncThunk(
  'uiData/fetchWorkflowRuns',
  ({ workflowRunsUrl }) => requestJson(workflowRunsUrl, { errorLabel: 'Failed to load workflow runs' }),
);

export const eventHandlers = {
  'workflows.snapshot': (state, payload) => {
    applyWorkflowItems(state, payload.items);
  },
  'workflow.updated': (state, payload) => {
    upsertWorkflowDetail(state, payload);
  },
  'workflow.deleted': (state, payload) => {
    removeWorkflow(state, payload.workflow_id);
  },
  'workflow.runs.snapshot': (state, payload) => {
    applyWorkflowRuns(state, payload.items);
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
    .addCase(fetchWorkflows.pending, (state) => {
      state.workflows.loading = true;
      state.workflows.error = null;
    })
    .addCase(fetchWorkflows.fulfilled, (state, action) => {
      applyWorkflowItems(state, action.payload?.items);
    })
    .addCase(fetchWorkflows.rejected, (state, action) => {
      state.workflows.loading = false;
      state.workflows.error = action.error?.message || 'Failed to load workflows';
    })
    .addCase(fetchWorkflowContent.fulfilled, (state, action) => {
      upsertWorkflowDetail(state, action.payload);
    })
    .addCase(saveWorkflow.fulfilled, (state, action) => {
      upsertWorkflowDetail(state, action.payload);
    })
    .addCase(createWorkflow.fulfilled, (state, action) => {
      upsertWorkflowDetail(state, action.payload);
    })
    .addCase(deleteWorkflow.fulfilled, (state, action) => {
      removeWorkflow(state, action.payload?.workflowId);
    })
    .addCase(pauseWorkflow.fulfilled, (state, action) => {
      upsertWorkflowDetail(state, action.payload);
    })
    .addCase(resumeWorkflow.fulfilled, (state, action) => {
      upsertWorkflowDetail(state, action.payload);
    })
    .addCase(triggerWorkflow.fulfilled, (state, action) => {
      if (action.payload?.run) {
        upsertWorkflowRun(state, action.payload.run);
      }
    })
    .addCase(fetchWorkflowRuns.pending, (state) => {
      state.workflows.runsLoading = true;
      state.workflows.runsError = null;
    })
    .addCase(fetchWorkflowRuns.fulfilled, (state, action) => {
      applyWorkflowRuns(state, action.payload?.items);
    })
    .addCase(fetchWorkflowRuns.rejected, (state, action) => {
      state.workflows.runsLoading = false;
      state.workflows.runsError = action.error?.message || 'Failed to load workflow runs';
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
