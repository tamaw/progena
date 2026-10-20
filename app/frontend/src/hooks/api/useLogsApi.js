import { useCallback } from 'react';
import { useConfig } from '../../context/ConfigContext';
import { requestAcknowledged, withQuery } from '../../store/http';
import { createUuid } from '../../lib/uuid';

/** Log retrieval acknowledgement with query params (Logs page). */
export function useLogsApi() {
  const { getApiUrl } = useConfig();

  const getLogs = useCallback(async (params = {}) => {
    const filtered = {};
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        filtered[key] = String(value);
      }
    });
    return requestAcknowledged(withQuery(getApiUrl('logs'), filtered), {
      requestId: createUuid(),
    });
  }, [getApiUrl]);

  return { getLogs };
}
