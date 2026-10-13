/** Knowledge catalog and markdown pages, kept live by UI stream events. */
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle } from './async';

export const initialState = {
  knowledge: {
    bases: [], loaded: false, loading: false, error: null, requestId: null,
    pagesByBaseId: {}, pageByKey: {}, revisionsByKey: {}, revisionContentByKey: {},
    searchResultsByQuery: {},
  },
};

function pageKey(baseId, path) { return `${baseId}/${path}`; }
function revisionKey(baseId, path, revisionId) { return `${baseId}/${path}@${revisionId}`; }
function applyKnowledgeBases(state, payload) {
  state.knowledge.bases = Array.isArray(payload.bases) ? payload.bases : [];
  state.knowledge.loaded = true;
  if (!state.knowledge.loading || payload.request_id === state.knowledge.requestId) {
    state.knowledge.loading = false; state.knowledge.error = null; state.knowledge.requestId = null;
  }
}

export const fetchKnowledge = createAcknowledgedThunk('uiData/fetchKnowledge', async ({ knowledgeUrl }, { requestId }) => requestAcknowledged(knowledgeUrl, { requestId }));
export const saveKnowledge = createAcknowledgedThunk('uiData/saveKnowledge', async ({ knowledgeUrl, bases }, { requestId }) => requestAcknowledged(knowledgeUrl, { method: 'PUT', body: { bases }, requestId }));
export const fetchKnowledgePages = createAcknowledgedThunk('uiData/fetchKnowledgePages', async ({ knowledgeUrl, baseId }, { requestId }) => requestAcknowledged(`${knowledgeUrl}/${encodeURIComponent(baseId)}/pages`, { requestId }));
export const fetchKnowledgePage = createAcknowledgedThunk('uiData/fetchKnowledgePage', async ({ knowledgeUrl, baseId, path }, { requestId }) => requestAcknowledged(`${knowledgeUrl}/${encodeURIComponent(baseId)}/page?path=${encodeURIComponent(path)}`, { requestId }));
export const deleteKnowledgePage = createAcknowledgedThunk('uiData/deleteKnowledgePage', async ({ knowledgeUrl, baseId, path }, { requestId }) => requestAcknowledged(`${knowledgeUrl}/${encodeURIComponent(baseId)}/page?path=${encodeURIComponent(path)}`, { method: 'DELETE', requestId }));
export const renameKnowledgePage = createAcknowledgedThunk('uiData/renameKnowledgePage', async ({ knowledgeUrl, baseId, path, newPath }, { requestId }) => requestAcknowledged(`${knowledgeUrl}/${encodeURIComponent(baseId)}/page/rename`, { method: 'POST', body: { path, new_path: newPath }, requestId }));
export const fetchKnowledgePageRevisions = createAcknowledgedThunk('uiData/fetchKnowledgePageRevisions', async ({ knowledgeUrl, baseId, path }, { requestId }) => requestAcknowledged(`${knowledgeUrl}/${encodeURIComponent(baseId)}/page/revisions?path=${encodeURIComponent(path)}`, { requestId }));
export const fetchKnowledgePageRevision = createAcknowledgedThunk('uiData/fetchKnowledgePageRevision', async ({ knowledgeUrl, baseId, path, revisionId }, { requestId }) => requestAcknowledged(`${knowledgeUrl}/${encodeURIComponent(baseId)}/page/revisions/${encodeURIComponent(revisionId)}?path=${encodeURIComponent(path)}`, { requestId }));
export const restoreKnowledgePageRevision = createAcknowledgedThunk('uiData/restoreKnowledgePageRevision', async ({ knowledgeUrl, baseId, path, revisionId }, { requestId }) => requestAcknowledged(`${knowledgeUrl}/${encodeURIComponent(baseId)}/page/revisions/${encodeURIComponent(revisionId)}/restore?path=${encodeURIComponent(path)}`, { method: 'POST', requestId }));
export const searchKnowledge = createAcknowledgedThunk('uiData/searchKnowledge', async ({ knowledgeUrl, query, limit = 8 }, { requestId }) => requestAcknowledged(`${knowledgeUrl}/search?q=${encodeURIComponent(query)}&limit=${limit}`, { requestId }));

function patchBaseIndexStatus(state, payload) {
  const bases = state.knowledge.bases;
  if (!Array.isArray(bases)) return;
  const idx = bases.findIndex((b) => b.id === payload.base_id);
  if (idx === -1) return;
  const base = bases[idx];
  bases[idx] = {
    ...base,
    index_status: payload.status !== undefined ? payload.status : base.index_status,
    index_pages: payload.pages !== undefined ? payload.pages : base.index_pages,
    indexed_pages: payload.indexed_pages !== undefined ? payload.indexed_pages : base.indexed_pages,
    index_error: payload.error !== undefined ? payload.error : base.index_error,
    indexed_at: payload.indexed_at !== undefined ? payload.indexed_at : base.indexed_at,
  };
}

export const eventHandlers = {
  'knowledge.snapshot': (state, payload) => applyKnowledgeBases(state, payload),
  'knowledge.pages.snapshot': (state, payload) => { state.knowledge.pagesByBaseId[payload.base_id] = Array.isArray(payload.pages) ? payload.pages : []; },
  'knowledge.page.loaded': (state, payload) => { state.knowledge.pageByKey[pageKey(payload.base_id, payload.path)] = payload; },
  'knowledge.page.revisions.loaded': (state, payload) => { state.knowledge.revisionsByKey[pageKey(payload.base_id, payload.path)] = Array.isArray(payload.revisions) ? payload.revisions : []; },
  'knowledge.page.revision.loaded': (state, payload) => { state.knowledge.revisionContentByKey[revisionKey(payload.base_id, payload.path, payload.revision_id)] = payload.content; },
  'knowledge.index.started': patchBaseIndexStatus,
  'knowledge.index.progress': patchBaseIndexStatus,
  'knowledge.index.completed': patchBaseIndexStatus,
  'knowledge.index.failed': patchBaseIndexStatus,
  'knowledge.search.results': (state, payload) => {
    state.knowledge.searchResultsByQuery[payload.query] = {
      query: payload.query,
      results: Array.isArray(payload.results) ? payload.results : [],
      error: payload.error || null,
    };
  },
};

export function buildExtraReducers(builder) {
  return addRequestLifecycle(builder, fetchKnowledge, { select: (state) => state.knowledge, errorMessage: 'Failed to load knowledge bases' });
}
export const selectKnowledgeBases = (state) => state.uiData.knowledge.bases;
export const selectKnowledgeState = (state) => state.uiData.knowledge;
export const selectKnowledgePages = (state, baseId) => state.uiData.knowledge.pagesByBaseId[baseId] || [];
export const selectKnowledgePage = (state, baseId, path) => state.uiData.knowledge.pageByKey[pageKey(baseId, path)] || null;
export const selectKnowledgeRevisions = (state, baseId, path) => state.uiData.knowledge.revisionsByKey[pageKey(baseId, path)] || [];
export const selectKnowledgeRevisionContent = (state, baseId, path, revisionId) => state.uiData.knowledge.revisionContentByKey[revisionKey(baseId, path, revisionId)] || null;
export const selectKnowledgeSearchResults = (state, query) => state.uiData.knowledge.searchResultsByQuery[query] || null;
