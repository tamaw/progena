import { UI_EVENT } from '../../realtime/eventVocabulary';
/** Global search slice — replaces the old knowledge-only search. */
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle } from '../../store/async';

export const initialState = {
  search: {
    resultsByQuery: {},
  },
};

export const searchAll = createAcknowledgedThunk(
  'uiData/searchAll',
  async ({ searchUrl, query }, { requestId }) =>
    requestAcknowledged(`${searchUrl}?q=${encodeURIComponent(query)}`, { requestId }),
);

export const eventHandlers = {
  [UI_EVENT.SEARCH_RESULTS]: (state, payload) => {
    state.search.resultsByQuery[payload.query] = {
      query: payload.query,
      sections: Array.isArray(payload.sections) ? payload.sections : [],
    };
  },
};

export function buildExtraReducers(builder) {
  return addRequestLifecycle(builder, searchAll, {
    select: (state) => state.search,
    errorMessage: 'Search failed',
  });
}

export const selectSearchResults = (state, query) =>
  state.uiData.search.resultsByQuery[query] || null;
