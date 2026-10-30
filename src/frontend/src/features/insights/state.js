import { UI_EVENT } from '../../realtime/eventVocabulary';
/**
 * Checkpoint-backed token usage snapshots. Each range/profile query owns a
 * cache entry so a late SSE response cannot replace the view the user chose.
 */
import { createSelector } from '@reduxjs/toolkit';
import { withQuery } from '../../store/http';
import {
  addKeyedRequestLifecycle,
  completeKeyedRequest,
  createAcknowledgedThunk,
  requestAcknowledged,
  selectKeyedRequest,
} from '../../store/async';

export const INSIGHT_RANGES = ['24h', '7d', '30d'];

export function normalizeInsightsQuery({ range = '7d', profile = null } = {}) {
  return {
    range: INSIGHT_RANGES.includes(range) ? range : '7d',
    profile: typeof profile === 'string' && profile.trim() ? profile.trim() : null,
  };
}

export function insightsQueryKey(query) {
  const { range, profile } = normalizeInsightsQuery(query);
  return JSON.stringify([range, profile]);
}

export const initialState = {
  insights: {
    snapshots: {},
    requests: {},
  },
};

export const fetchTokenUsage = createAcknowledgedThunk(
  'uiData/fetchTokenUsage',
  ({ tokenUsageUrl, range, profile }, { requestId }) => {
    const query = normalizeInsightsQuery({ range, profile });
    return requestAcknowledged(withQuery(tokenUsageUrl, {
      range: query.range,
      ...(query.profile ? { profile: query.profile } : {}),
    }), { requestId });
  },
);

export const eventHandlers = {
  [UI_EVENT.INSIGHTS_TOKEN_USAGE_SNAPSHOT]: (state, payload) => {
    const query = normalizeInsightsQuery(payload);
    const key = insightsQueryKey(query);
    const request = state.insights.requests[key];

    // Snapshots are request responses, not unsolicited updates. Reject old
    // responses even after a newer request has already completed.
    if (!request?.requestId || payload.request_id !== request.requestId) {
      return;
    }

    state.insights.snapshots[key] = {
      ...payload,
      range: query.range,
      profile: query.profile,
    };
    completeKeyedRequest(state.insights.requests, key, payload.request_id);
  },
};

export function buildExtraReducers(builder) {
  return addKeyedRequestLifecycle(builder, fetchTokenUsage, {
    selectRequests: (state) => state.insights.requests,
    keyFromArgs: insightsQueryKey,
    errorMessage: 'Failed to load token usage',
  });
}

const selectInsightsDomain = (state) => state.uiData.insights;
export const selectInsightsSnapshot = createSelector(
  [selectInsightsDomain, (_state, query) => insightsQueryKey(query)],
  (insights, key) => insights.snapshots[key] || null,
);
export const selectInsightsRequest = createSelector(
  [selectInsightsDomain, (_state, query) => insightsQueryKey(query)],
  (insights, key) => selectKeyedRequest(insights.requests, key),
);
export const selectInsightsQueryState = createSelector(
  [selectInsightsSnapshot, selectInsightsRequest],
  (snapshot, request) => ({ snapshot, ...request }),
);
