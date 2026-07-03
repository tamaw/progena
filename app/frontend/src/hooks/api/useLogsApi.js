import { useCallback } from 'react';
import { useApiClient } from './useApiClient';

/** Log retrieval with query params (Logs page). */
export function useLogsApi() {
  const { request } = useApiClient();

  const getLogs = useCallback((params = {}) => {
    const searchParams = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        searchParams.set(key, String(value));
      }
    });
    const query = searchParams.size > 0 ? `?${searchParams.toString()}` : '';
    return request(`/api/ui/logs${query}`);
  }, [request]);

  return { getLogs };
}
