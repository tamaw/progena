import { useCallback, useEffect, useMemo, useRef } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { selectRealtimeStreamReady } from '../../realtime/realtimeSlice';
import {
  fetchRunTrace, fetchRuns, normalizeRunsQuery, RUN_KINDS, runsQueryKey, selectRunTrace, selectRunTraceRequest,
  selectRunsQueryState,
} from './state';
import { watchRunTrace } from './runSubscriptions';

export function useRunsQuery({ runsUrl, kind, status, q, cursor, period, sort }) {
  const dispatch = useDispatch();
  const streamReady = useSelector(selectRealtimeStreamReady);
  const normalizedQuery = useMemo(
    () => normalizeRunsQuery({ kind, status, q, cursor, period, sort }),
    [kind, status, q, cursor, period, sort],
  );
  const queryState = useSelector((state) => selectRunsQueryState(state, normalizedQuery));
  const refresh = useCallback(({ resync = false } = {}) => {
    if (!streamReady || !runsUrl) return;
    dispatch(fetchRuns({ runsUrl, ...normalizedQuery, resync }));
  }, [dispatch, normalizedQuery, runsUrl, streamReady]);
  const connection = useRef({ ready: false, generation: 0, loaded: new Map() });
  const queryKey = runsQueryKey(normalizedQuery);

  useEffect(() => {
    if (!streamReady) {
      connection.current.ready = false;
      return;
    }
    if (!connection.current.ready) {
      connection.current.ready = true;
      connection.current.generation += 1;
    }
    if (!runsUrl || queryState.loading) return;
    const firstLoad = connection.current.loaded.get(queryKey) !== connection.current.generation;
    if (firstLoad) {
      connection.current.loaded.set(queryKey, connection.current.generation);
      refresh({ resync: true });
    }
  }, [queryKey, queryState.loading, refresh, runsUrl, streamReady]);

  return { ...queryState, query: normalizedQuery, refresh, streamReady };
}

export function useRunTrace({ runsUrl, subscriptionUrl, kind, runId, enabled }) {
  const dispatch = useDispatch();
  const streamReady = useSelector(selectRealtimeStreamReady);
  const trace = useSelector((state) => selectRunTrace(state, kind, runId));
  const request = useSelector((state) => selectRunTraceRequest(state, kind, runId));
  const canWatch = enabled && RUN_KINDS.includes(kind) && Boolean(runId);
  const refresh = useCallback(() => {
    if (!streamReady || !canWatch || !runsUrl) return;
    dispatch(fetchRunTrace({ runsUrl, kind, runId }));
  }, [canWatch, dispatch, kind, runId, runsUrl, streamReady]);

  useEffect(() => {
    if (!streamReady || !canWatch || !runsUrl || !subscriptionUrl) return undefined;
    refresh();
    return watchRunTrace(subscriptionUrl, kind, runId);
  }, [canWatch, kind, refresh, runId, runsUrl, streamReady, subscriptionUrl]);

  return { trace, ...request, refresh, streamReady };
}
