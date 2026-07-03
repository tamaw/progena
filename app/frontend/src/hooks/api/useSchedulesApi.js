import { useCallback } from 'react';
import { useApiClient } from './useApiClient';

/** Schedule listing, creation and re-trigger (Schedules page, Workspace context). */
export function useSchedulesApi() {
  const { request } = useApiClient();

  const getSchedules = useCallback(() => request('schedules'), [request]);

  const createSchedule = useCallback(
    (payload) => request('schedules', { method: 'POST', body: JSON.stringify(payload) }),
    [request],
  );

  const retriggerSchedule = useCallback(
    // Use the full path: getApiUrl only maps exact endpoint keys (e.g.
    // "schedules"), so a sub-path must include the /api/ui prefix itself.
    (scheduleId, text) => request(`/api/ui/schedules/${scheduleId}/trigger`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
    [request],
  );

  return { getSchedules, createSchedule, retriggerSchedule };
}
