/**
 * Prompts domain: the workspace prompt library, kept live by `prompts.snapshot`
 * events on /api/ui/stream and seeded via REST when the Prompts page mounts.
 * Each prompt is the raw API shape so edits PUT the file content straight back.
 */
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle } from './async';
import { withQuery } from '../http';

export const initialState = {
  prompts: {
    items: [],
    loaded: false,
    loading: false,
    error: null,
    snapshotRequestId: null,
    requestId: null,
    contentByPath: {},
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
export const fetchPrompts = createAcknowledgedThunk('uiData/fetchPrompts', async ({ promptsUrl, agent } = {}, { requestId }) => {
  return requestAcknowledged(
    agent ? withQuery(promptsUrl, { agent }) : promptsUrl,
    { requestId },
  );
});

// Persist edits to one prompt file. The backend saves it and broadcasts
// `prompts.snapshot`; the acknowledgement triggers the snapshot event
// which reconciles state.
export const savePrompt = createAcknowledgedThunk('uiData/savePrompt', async ({ promptsUrl, path, content }, { requestId }) => {
  return requestAcknowledged(`${promptsUrl}/content`, {
    method: 'PUT',
    body: { path, content },
    requestId,
  });
});

// Fetch one prompt's full body on demand (list items carry metadata only).
export const fetchPromptContent = createAcknowledgedThunk('uiData/fetchPromptContent', async ({ promptsUrl, path }, { requestId }) => {
  const acknowledgement = await requestAcknowledged(
    withQuery(`${promptsUrl}/content`, { path }),
    { requestId },
  );
  return { ...acknowledgement, path };
});

export const eventHandlers = {
  'prompts.snapshot': (state, payload) => {
    applyPrompts(state, payload.prompts, payload.request_id || null);
  },
  'prompt.content.updated': (state, payload) => {
    if (typeof payload?.path === 'string' && payload.path) {
      state.prompts.contentByPath[payload.path] = payload;
    }
  },
};

export function buildExtraReducers(builder) {
  return addRequestLifecycle(builder, fetchPrompts, {
    select: (state) => state.prompts,
    errorMessage: 'Failed to load prompts',
  });
}
export const selectPrompts = (state) => state.uiData.prompts.items;
export const selectPromptsState = (state) => state.uiData.prompts;
export const selectPromptContentEntry = (state, path) => (
  path ? state.uiData.prompts.contentByPath[path] || null : null
);
