import { useCallback, useEffect } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useConfig } from '../../context/ConfigContext';
import { fetchTokenUsage, normalizeInsightsQuery, selectInsightsQueryState } from './state';
import { selectRealtimeStreamReady } from '../../realtime/realtimeSlice';

export function useInsightsQuery(query) {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const normalizedQuery = normalizeInsightsQuery(query);
  const { range, profile } = normalizedQuery;
  const streamReady = useSelector(selectRealtimeStreamReady);
  const state = useSelector((rootState) => selectInsightsQueryState(rootState, normalizedQuery));
  const tokenUsageUrl = getApiUrl('tokenUsage');

  const refresh = useCallback(() => {
    if (!streamReady) return;
    dispatch(fetchTokenUsage({ tokenUsageUrl, range, profile }));
  }, [dispatch, profile, range, streamReady, tokenUsageUrl]);

  // A stream reconnect has no replay guarantee. Reissue the visible query once
  // the replacement stream is ready, rather than trusting a cached snapshot.
  useEffect(() => {
    refresh();
  }, [refresh]);

  return { ...state, query: normalizedQuery, refresh, streamReady };
}
