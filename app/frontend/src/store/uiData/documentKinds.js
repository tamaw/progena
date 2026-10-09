/**
 * Document-kinds domain: the canonical registry of `store` table document key
 * prefixes (see app.progenitor.document_kinds), used to back the workflow
 * event trigger's "document kind" picker. Static for the process lifetime of
 * the backend (it's a Python constant, not a persisted document), so it's
 * fetched once and never needs a dedicated write path.
 */
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle } from './async';

export const initialState = {
  documentKinds: {
    items: [],
    loaded: false,
    loading: false,
    error: null,
    snapshotRequestId: null,
    requestId: null,
  },
};

function applyDocumentKinds(state, items, requestId = null) {
  state.documentKinds.items = Array.isArray(items) ? items : [];
  state.documentKinds.loaded = true;
  state.documentKinds.snapshotRequestId = requestId;
  if (!state.documentKinds.loading || requestId === state.documentKinds.requestId) {
    state.documentKinds.loading = false;
    state.documentKinds.error = null;
    state.documentKinds.requestId = null;
  }
}

// Load the document-kind registry. The GET triggers a
// `document_kinds.snapshot` broadcast that hydrates state from the realtime
// stream.
export const fetchDocumentKinds = createAcknowledgedThunk('uiData/fetchDocumentKinds', async ({ documentKindsUrl } = {}, { requestId }) => {
  return requestAcknowledged(documentKindsUrl, { requestId });
});

export const eventHandlers = {
  'document_kinds.snapshot': (state, payload) => {
    applyDocumentKinds(state, payload.items, payload.request_id || null);
  },
};

export function buildExtraReducers(builder) {
  return addRequestLifecycle(builder, fetchDocumentKinds, {
    select: (state) => state.documentKinds,
    errorMessage: 'Failed to load document kinds',
  });
}

export const selectDocumentKinds = (state) => state.uiData.documentKinds.items;
export const selectDocumentKindsState = (state) => state.uiData.documentKinds;
