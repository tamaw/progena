/**
 * Prompts domain: the workspace prompt library, kept live by `prompts.snapshot`
 * events on /api/ui/stream and seeded via REST when the Prompts page mounts.
 * Each prompt is the raw API shape so edits PUT the file content straight back.
 */
import { createAsyncThunk } from '@reduxjs/toolkit';
import { requestJson, withQuery } from '../http';

export const initialState = {
  prompts: {
    items: [],
    loaded: false,
    loading: false,
    error: null,
  },
};

function applyPrompts(state, items) {
  state.prompts.items = Array.isArray(items) ? items : [];
  state.prompts.loaded = true;
  state.prompts.loading = false;
  state.prompts.error = null;
}

function upsertPrompt(state, prompt) {
  if (!prompt?.path) {
    return;
  }
  const index = state.prompts.items.findIndex((item) => item.path === prompt.path);
  if (index >= 0) {
    state.prompts.items[index] = prompt;
  } else {
    state.prompts.items.push(prompt);
  }
}

// Load the prompt catalogue. The response carries the list (so the page renders
// immediately) and the GET also triggers a `prompts.snapshot` broadcast that
// keeps other open tabs in sync.
export const fetchPrompts = createAsyncThunk(
  'uiData/fetchPrompts',
  ({ promptsUrl, agent } = {}) => requestJson(
    agent ? withQuery(promptsUrl, { agent }) : promptsUrl,
    { errorLabel: 'Failed to load prompts' },
  ),
);

// Persist edits to one prompt file. The backend saves it and broadcasts
// `prompts.snapshot`; the response carries the saved record so we upsert it
// immediately (the snapshot then reconciles).
export const savePrompt = createAsyncThunk(
  'uiData/savePrompt',
  ({ promptsUrl, path, content }) => requestJson(`${promptsUrl}/content`, {
    method: 'PUT',
    body: { path, content },
    errorLabel: 'Failed to save prompt:',
  }),
);

export const eventHandlers = {
  'prompts.snapshot': (state, payload) => {
    applyPrompts(state, payload.prompts);
  },
};

export function buildExtraReducers(builder) {
  builder
    .addCase(fetchPrompts.pending, (state) => {
      state.prompts.loading = true;
      state.prompts.error = null;
    })
    .addCase(fetchPrompts.fulfilled, (state, action) => {
      applyPrompts(state, action.payload?.prompts);
    })
    .addCase(fetchPrompts.rejected, (state, action) => {
      state.prompts.loading = false;
      state.prompts.error = action.error?.message || 'Failed to load prompts';
    })
    .addCase(savePrompt.fulfilled, (state, action) => {
      upsertPrompt(state, action.payload);
    });
}

export const selectPrompts = (state) => state.uiData.prompts.items;
export const selectPromptsState = (state) => state.uiData.prompts;
