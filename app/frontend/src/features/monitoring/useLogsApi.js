import { useCallback } from 'react';
import { useDispatch } from 'react-redux';
import { useConfig } from '../../context/ConfigContext';
import { fetchLogs } from './logsState';

/** Bind log retrieval to the active workspace and shared request lifecycle. */
export function useLogsApi() {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const getLogs = useCallback(
    (params = {}) => dispatch(fetchLogs({ logsUrl: getApiUrl('logs'), params })).unwrap(),
    [dispatch, getApiUrl],
  );
  return { getLogs };
}
