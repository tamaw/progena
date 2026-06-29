/**
 * Knowledge domain: knowledge-base settings, kept live by `knowledge.snapshot`
 * events on /api/ui/stream and seeded via REST when the Knowledge page mounts.
 * Each base is the raw API shape (snake_case) so edits can PUT the document back.
 */
import { createAsyncThunk } from '@reduxjs/toolkit';
import { requestJson } from '../http';

export const initialState = {
  knowledge: {
    bases: [],
    loaded: false,
    loading: false,
    error: null,
  },
};

function applyKnowledgeBases(state, bases) {
  state.knowledge.bases = Array.isArray(bases) ? bases : [];
  state.knowledge.loaded = true;
  state.knowledge.loading = false;
  state.knowledge.error = null;
}

// Acknowledge a knowledge-base list request; the snapshot arrives on the
// realtime stream as `knowledge.snapshot` (the GET also triggers the broadcast).
// The response carries the bases too, so the page populates even before the SSE
// frame lands.
export const fetchKnowledge = createAsyncThunk(
  'uiData/fetchKnowledge',
  ({ knowledgeUrl }) => requestJson(knowledgeUrl, { errorLabel: 'Failed to load knowledge bases' }),
);

// Persist the full knowledge-base settings document. The backend validates it,
// saves it, and broadcasts `knowledge.snapshot`; the response also carries the
// snapshot so callers can read back the server-assigned ids.
export const saveKnowledge = createAsyncThunk(
  'uiData/saveKnowledge',
  ({ knowledgeUrl, bases }) => requestJson(knowledgeUrl, {
    method: 'PUT',
    body: { bases },
    errorLabel: 'Failed to save knowledge bases:',
  }),
);

// Walk + embed one folder source into SQL on the server, then update the doc.
export const indexKnowledgeSource = createAsyncThunk(
  'uiData/indexKnowledgeSource',
  ({ knowledgeUrl, baseId, sourceId }) => requestJson(
    `${knowledgeUrl}/${encodeURIComponent(baseId)}/sources/${encodeURIComponent(sourceId)}/index`,
    { method: 'POST', errorLabel: 'Failed to index source' },
  ),
);

export const eventHandlers = {
  'knowledge.snapshot': (state, payload) => {
    applyKnowledgeBases(state, payload.bases);
  },
};

export function buildExtraReducers(builder) {
  builder
    .addCase(fetchKnowledge.pending, (state) => {
      state.knowledge.loading = true;
      state.knowledge.error = null;
    })
    .addCase(fetchKnowledge.fulfilled, (state, action) => {
      applyKnowledgeBases(state, action.payload?.bases);
    })
    .addCase(fetchKnowledge.rejected, (state, action) => {
      state.knowledge.loading = false;
      state.knowledge.error = action.error?.message || 'Failed to load knowledge bases';
    })
    .addCase(saveKnowledge.fulfilled, (state, action) => {
      applyKnowledgeBases(state, action.payload?.bases);
    })
    .addCase(indexKnowledgeSource.fulfilled, (state, action) => {
      applyKnowledgeBases(state, action.payload?.bases);
    });
}

export const selectKnowledgeBases = (state) => state.uiData.knowledge.bases;
export const selectKnowledgeState = (state) => state.uiData.knowledge;
