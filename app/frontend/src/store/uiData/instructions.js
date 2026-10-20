/**
 * Instructions: the agents' markdown instructions, kept live by UI stream
 * events. The list carries front matter summaries; one instruction's content,
 * its revisions and a viewed revision are loaded on demand.
 */
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle, addKeyedRequestLifecycle, completeKeyedRequest, selectKeyedRequest } from './async';
import { withQuery } from '../http';

export const initialState = {
  instructions: {
    items: [],
    loaded: false,
    loading: false,
    error: null,
    requestId: null,
    byPath: {},
    revisionsByPath: {},
    revisionByKey: {},
    contentRequestByPath: {},
    revisionsRequestByPath: {},
    revisionRequestByKey: {},
  },
};

const revisionKey = (path, revisionId) => `${path}@${revisionId}`;

export const fetchInstructions = createAcknowledgedThunk(
  'uiData/fetchInstructions',
  async ({ instructionsUrl }, { requestId }) => requestAcknowledged(instructionsUrl, { requestId }),
);

export const fetchInstruction = createAcknowledgedThunk(
  'uiData/fetchInstruction',
  async ({ instructionsUrl, path }, { requestId }) => requestAcknowledged(
    withQuery(`${instructionsUrl}/content`, { path }),
    { requestId },
  ),
);

export const saveInstruction = createAcknowledgedThunk(
  'uiData/saveInstruction',
  async ({ instructionsUrl, path, content }, { requestId }) => requestAcknowledged(
    `${instructionsUrl}/content`,
    { method: 'PUT', body: { path, content }, requestId },
  ),
);

export const fetchInstructionRevisions = createAcknowledgedThunk(
  'uiData/fetchInstructionRevisions',
  async ({ instructionsUrl, path }, { requestId }) => requestAcknowledged(
    withQuery(`${instructionsUrl}/revisions`, { path }),
    { requestId },
  ),
);

export const fetchInstructionRevision = createAcknowledgedThunk(
  'uiData/fetchInstructionRevision',
  async ({ instructionsUrl, path, revisionId }, { requestId }) => requestAcknowledged(
    withQuery(`${instructionsUrl}/revisions/${encodeURIComponent(revisionId)}`, { path }),
    { requestId },
  ),
);

export const restoreInstructionRevision = createAcknowledgedThunk(
  'uiData/restoreInstructionRevision',
  async ({ instructionsUrl, path, revisionId }, { requestId }) => requestAcknowledged(
    withQuery(`${instructionsUrl}/revisions/${encodeURIComponent(revisionId)}/restore`, { path }),
    { method: 'POST', requestId },
  ),
);

export const eventHandlers = {
  'instructions.snapshot': (state, payload) => {
    const slice = state.instructions;
    slice.items = Array.isArray(payload.instructions) ? payload.instructions : [];
    slice.loaded = true;
    if (!slice.loading || payload.request_id === slice.requestId) {
      slice.loading = false;
      slice.error = null;
      slice.requestId = null;
    }
  },
  'instruction.loaded': (state, payload) => {
    if (typeof payload?.path === 'string' && payload.path) {
      state.instructions.byPath[payload.path] = payload;
      completeKeyedRequest(state.instructions.contentRequestByPath, payload.path, payload.request_id);
    }
  },
  'instruction.revisions.loaded': (state, payload) => {
    state.instructions.revisionsByPath[payload.path] = Array.isArray(payload.revisions) ? payload.revisions : [];
    completeKeyedRequest(state.instructions.revisionsRequestByPath, payload.path, payload.request_id);
  },
  'instruction.revision.loaded': (state, payload) => {
    const key = revisionKey(payload.path, payload.revision_id);
    state.instructions.revisionByKey[key] = payload;
    completeKeyedRequest(state.instructions.revisionRequestByKey, key, payload.request_id);
  },
};

export function buildExtraReducers(builder) {
  addRequestLifecycle(builder, fetchInstructions, {
    select: (state) => state.instructions,
    errorMessage: 'Failed to load instructions',
  });
  addKeyedRequestLifecycle(builder, fetchInstruction, {
    selectRequests: (state) => state.instructions.contentRequestByPath,
    keyFromArgs: ({ path } = {}) => path,
    errorMessage: 'Failed to load instruction content',
  });
  addKeyedRequestLifecycle(builder, fetchInstructionRevisions, {
    selectRequests: (state) => state.instructions.revisionsRequestByPath,
    keyFromArgs: ({ path } = {}) => path,
    errorMessage: 'Failed to load instruction revisions',
  });
  return addKeyedRequestLifecycle(builder, fetchInstructionRevision, {
    selectRequests: (state) => state.instructions.revisionRequestByKey,
    keyFromArgs: ({ path, revisionId } = {}) => (
      path && revisionId ? revisionKey(path, revisionId) : null
    ),
    errorMessage: 'Failed to load instruction revision',
  });
}

export const selectInstructions = (state) => state.uiData.instructions.items;
export const selectInstructionsState = (state) => state.uiData.instructions;
export const selectInstruction = (state, path) => (path ? state.uiData.instructions.byPath[path] || null : null);
export const selectInstructionRevisions = (state, path) => (
  path ? state.uiData.instructions.revisionsByPath[path] || [] : []
);
// One captured revision `{ content, front_matter, body }`, or null until loaded.
export const selectInstructionRevision = (state, path, revisionId) => (
  path && revisionId ? state.uiData.instructions.revisionByKey[revisionKey(path, revisionId)] || null : null
);
export const selectInstructionContentRequest = (state, path) => (
  selectKeyedRequest(state.uiData.instructions.contentRequestByPath, path)
);
export const selectInstructionRevisionsRequest = (state, path) => (
  selectKeyedRequest(state.uiData.instructions.revisionsRequestByPath, path)
);
export const selectInstructionRevisionRequest = (state, path, revisionId) => (
  selectKeyedRequest(
    state.uiData.instructions.revisionRequestByKey,
    path && revisionId ? revisionKey(path, revisionId) : null,
  )
);
