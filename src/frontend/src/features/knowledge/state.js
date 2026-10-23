/** Knowledge catalog and markdown pages, kept live by UI stream events. */
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle, addKeyedRequestLifecycle, completeKeyedRequest, selectKeyedRequest } from '../../store/async';

export const initialState = {
  knowledge: {
    bases: [], loaded: false, loading: false, error: null, requestId: null,
    pagesByBaseId: {}, pageByKey: {}, revisionsByKey: {}, revisionByKey: {},
    pageRequestByKey: {}, revisionsRequestByKey: {}, revisionRequestByKey: {},
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
export const fetchKnowledgePageRevisions = createAcknowledgedThunk('uiData/fetchKnowledgePageRevisions', async ({ knowledgeUrl, baseId, path }, { requestId }) => requestAcknowledged(`${knowledgeUrl}/${encodeURIComponent(baseId)}/page/revisions?path=${encodeURIComponent(path)}`, { requestId }));
export const fetchKnowledgePageRevision = createAcknowledgedThunk('uiData/fetchKnowledgePageRevision', async ({ knowledgeUrl, baseId, path, revisionId }, { requestId }) => requestAcknowledged(`${knowledgeUrl}/${encodeURIComponent(baseId)}/page/revisions/${encodeURIComponent(revisionId)}?path=${encodeURIComponent(path)}`, { requestId }));

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
  'knowledge.page.loaded': (state, payload) => {
    const key = pageKey(payload.base_id, payload.path);
    state.knowledge.pageByKey[key] = payload;
    completeKeyedRequest(state.knowledge.pageRequestByKey, key, payload.request_id);
  },
  'knowledge.page.revisions.loaded': (state, payload) => {
    const key = pageKey(payload.base_id, payload.path);
    state.knowledge.revisionsByKey[key] = Array.isArray(payload.revisions) ? payload.revisions : [];
    completeKeyedRequest(state.knowledge.revisionsRequestByKey, key, payload.request_id);
  },
  'knowledge.page.revision.loaded': (state, payload) => {
    const key = revisionKey(payload.base_id, payload.path, payload.revision_id);
    state.knowledge.revisionByKey[key] = {
      content: payload.content,
      front_matter: payload.front_matter || null,
    };
    completeKeyedRequest(state.knowledge.revisionRequestByKey, key, payload.request_id);
  },
  'knowledge.index.started': patchBaseIndexStatus,
  'knowledge.index.progress': patchBaseIndexStatus,
  'knowledge.index.completed': patchBaseIndexStatus,
  'knowledge.index.failed': patchBaseIndexStatus,
};

export function buildExtraReducers(builder) {
  addRequestLifecycle(builder, fetchKnowledge, { select: (state) => state.knowledge, errorMessage: 'Failed to load knowledge bases' });
  addKeyedRequestLifecycle(builder, fetchKnowledgePage, {
    selectRequests: (state) => state.knowledge.pageRequestByKey,
    keyFromArgs: ({ baseId, path } = {}) => (
      baseId && path ? pageKey(baseId, path) : null
    ),
    errorMessage: 'Failed to load knowledge page',
  });
  addKeyedRequestLifecycle(builder, fetchKnowledgePageRevisions, {
    selectRequests: (state) => state.knowledge.revisionsRequestByKey,
    keyFromArgs: ({ baseId, path } = {}) => (
      baseId && path ? pageKey(baseId, path) : null
    ),
    errorMessage: 'Failed to load knowledge page revisions',
  });
  return addKeyedRequestLifecycle(builder, fetchKnowledgePageRevision, {
    selectRequests: (state) => state.knowledge.revisionRequestByKey,
    keyFromArgs: ({ baseId, path, revisionId } = {}) => (
      baseId && path && revisionId ? revisionKey(baseId, path, revisionId) : null
    ),
    errorMessage: 'Failed to load knowledge page revision',
  });
}
export const selectKnowledgeBases = (state) => state.uiData.knowledge.bases;
export const selectKnowledgeState = (state) => state.uiData.knowledge;
export const selectKnowledgePages = (state, baseId) => state.uiData.knowledge.pagesByBaseId[baseId] || [];
export const selectKnowledgePage = (state, baseId, path) => state.uiData.knowledge.pageByKey[pageKey(baseId, path)] || null;
export const selectKnowledgeRevisions = (state, baseId, path) => state.uiData.knowledge.revisionsByKey[pageKey(baseId, path)] || [];
// One captured revision: `{ content, front_matter }`, or null until it has loaded.
export const selectKnowledgeRevision = (state, baseId, path, revisionId) => state.uiData.knowledge.revisionByKey[revisionKey(baseId, path, revisionId)] || null;
export const selectKnowledgePageRequest = (state, baseId, path) => (
  selectKeyedRequest(state.uiData.knowledge.pageRequestByKey, baseId && path ? pageKey(baseId, path) : null)
);
export const selectKnowledgePageRevisionsRequest = (state, baseId, path) => (
  selectKeyedRequest(state.uiData.knowledge.revisionsRequestByKey, baseId && path ? pageKey(baseId, path) : null)
);
export const selectKnowledgePageRevisionRequest = (state, baseId, path, revisionId) => (
  selectKeyedRequest(
    state.uiData.knowledge.revisionRequestByKey,
    baseId && path && revisionId ? revisionKey(baseId, path, revisionId) : null,
  )
);
