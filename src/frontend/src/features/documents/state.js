import { UI_EVENT } from '../../realtime/eventVocabulary';
/**
 * Document-types domain: the canonical registry of `store` table document
 * types (see document events contract), used to back workflow node pickers.
 * Fetched once and kept live by `document_types.snapshot` on /api/ui/stream.
 */
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle } from '../../store/async';

export const initialState = {
  documentTypes: {
    items: [],
    loaded: false,
    loading: false,
    error: null,
    snapshotRequestId: null,
    requestId: null,
  },
};

function applyDocumentTypes(state, items, requestId = null) {
  state.documentTypes.items = Array.isArray(items) ? items : [];
  state.documentTypes.loaded = true;
  state.documentTypes.snapshotRequestId = requestId;
  if (!state.documentTypes.loading || requestId === state.documentTypes.requestId) {
    state.documentTypes.loading = false;
    state.documentTypes.error = null;
    state.documentTypes.requestId = null;
  }
}

export const fetchDocumentTypes = createAcknowledgedThunk('uiData/fetchDocumentTypes', async ({ documentTypesUrl } = {}, { requestId }) => {
  return requestAcknowledged(documentTypesUrl, { requestId });
});

export const eventHandlers = {
  [UI_EVENT.DOCUMENT_TYPES_SNAPSHOT]: (state, payload) => {
    applyDocumentTypes(state, payload.items, payload.request_id || null);
  },
};

export function buildExtraReducers(builder) {
  return addRequestLifecycle(builder, fetchDocumentTypes, {
    select: (state) => state.documentTypes,
    errorMessage: 'Failed to load document types',
  });
}

export const selectDocumentTypes = (state) => state.uiData.documentTypes.items;
export const selectDocumentTypesState = (state) => state.uiData.documentTypes;
