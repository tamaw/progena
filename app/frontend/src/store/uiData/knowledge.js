/**
 * Knowledge domain: knowledge-base settings, kept live by `knowledge.snapshot`
 * events on /api/ui/stream and seeded via REST when the Knowledge page mounts.
 * Each base is the raw API shape (snake_case) so edits can PUT the document back.
 */
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle } from './async';

export const initialState = {
  knowledge: {
    bases: [],
    loaded: false,
    loading: false,
    error: null,
    requestId: null,
    search: {
      query: '',
      count: 0,
      results: [],
      requestId: null,
    },
  },
};

function applyKnowledgeBases(state, payload) {
  state.knowledge.bases = Array.isArray(payload.bases) ? payload.bases : [];
  state.knowledge.loaded = true;
  if (!state.knowledge.loading || payload.request_id === state.knowledge.requestId) {
    state.knowledge.loading = false;
    state.knowledge.error = null;
    state.knowledge.requestId = null;
  }
}

// Acknowledge a knowledge-base list request; the snapshot arrives on the
// realtime stream as `knowledge.snapshot`.
export const fetchKnowledge = createAcknowledgedThunk('uiData/fetchKnowledge', async ({ knowledgeUrl }, { requestId }) => {
  return requestAcknowledged(knowledgeUrl, { requestId });
});

// Persist the full knowledge-base settings document. The backend validates it,
// saves it, and broadcasts `knowledge.snapshot` on the realtime stream.
export const saveKnowledge = createAcknowledgedThunk('uiData/saveKnowledge', async ({ knowledgeUrl, bases }, { requestId }) => {
  return requestAcknowledged(knowledgeUrl, { method: 'PUT', body: { bases }, requestId });
});

// Walk + embed one folder source into SQL on the server, then update the doc.
export const indexKnowledgeSource = createAcknowledgedThunk('uiData/indexKnowledgeSource', async ({ knowledgeUrl, baseId, sourceId }, { requestId }) => {
  return requestAcknowledged(
    `${knowledgeUrl}/${encodeURIComponent(baseId)}/sources/${encodeURIComponent(sourceId)}/index`,
    { method: 'POST', requestId },
  );
});

export const eventHandlers = {
  'knowledge.snapshot': (state, payload) => {
    applyKnowledgeBases(state, payload);
  },
  'knowledge.search.results': (state, payload) => {
    state.knowledge.search = {
      query: typeof payload.query === 'string' ? payload.query : '',
      count: Number.isFinite(payload.count) ? payload.count : 0,
      results: Array.isArray(payload.results) ? payload.results : [],
      requestId: payload.request_id || null,
    };
  },
};

export function buildExtraReducers(builder) {
  return addRequestLifecycle(builder, fetchKnowledge, {
    select: (state) => state.knowledge,
    errorMessage: 'Failed to load knowledge bases',
  });
}

export const selectKnowledgeBases = (state) => state.uiData.knowledge.bases;
export const selectKnowledgeState = (state) => state.uiData.knowledge;

export const selectKnowledgeSearch = (state) => state.uiData.knowledge.search;