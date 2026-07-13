/**
 * Prompts domain: the workspace prompt library, kept live by `prompts.snapshot`
 * events on /api/ui/stream and seeded via REST when the Prompts page mounts.
 * Each prompt is the raw API shape so edits PUT the file content straight back.
 */
import { createAsyncThunk } from '@reduxjs/toolkit';
import { createOperationRequestId, requestAcknowledged, withQuery } from '../http';

export const initialState = {
  prompts: {
    items: [],
    loaded: false,
    loading: false,
    error: null,
    snapshotRequestId: null,
    requestId: null,
  },
};

function applyPrompts(state, items, requestId = null) {
  state.prompts.items = Array.isArray(items) ? items : [];
  state.prompts.loaded = true;
  state.prompts.snapshotRequestId = requestId;
  if (!state.prompts.loading || requestId === state.prompts.requestId) {
    state.prompts.loading = false;
    state.prompts.error = null;
    state.prompts.requestId = null;
  }
}


// Load the prompt catalogue. The GET triggers a `prompts.snapshot` broadcast
// that hydrates state from the realtime stream.
export const fetchPrompts = createAsyncThunk('uiData/fetchPrompts', async ({ promptsUrl, agent } = {}, { requestId }) => {
  return requestAcknowledged(
    agent ? withQuery(promptsUrl, { agent }) : promptsUrl,
    { requestId },
  );
}, { idGenerator: createOperationRequestId });

// Persist edits to one prompt file. The backend saves it and broadcasts
// `prompts.snapshot`; the acknowledgement triggers the snapshot event
// which reconciles state.
export const savePrompt = createAsyncThunk('uiData/savePrompt', async ({ promptsUrl, path, content }, { requestId }) => {
  return requestAcknowledged(`${promptsUrl}/content`, {
    method: 'PUT',
    body: { path, content },
    requestId,
  });
}, { idGenerator: createOperationRequestId });

export const eventHandlers = {
  'prompts.snapshot': (state, payload) => {
    applyPrompts(state, payload.prompts, payload.request_id || null);
  },
};

export function buildExtraReducers(builder) {
  builder
    .addCase(fetchPrompts.pending, (state, action) => {
      state.prompts.loading = true;
      state.prompts.error = null;
      state.prompts.requestId = action.meta.requestId;
    })
    .addCase(fetchPrompts.rejected, (state, action) => {
      if (state.prompts.requestId !== action.meta.requestId) {
        return;
      }
      state.prompts.loading = false;
      state.prompts.error = action.error?.message || 'Failed to load prompts';
      state.prompts.requestId = null;
    });
}
export const selectPrompts = (state) => state.uiData.prompts.items;
export const selectPromptsState = (state) => state.uiData.prompts;
