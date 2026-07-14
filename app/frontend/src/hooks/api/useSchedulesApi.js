import { useCallback } from 'react';
import { useConfig } from '../../context/ConfigContext';
import { requestAcknowledged } from '../../store/http';

/** Schedule operations acknowledge immediately; state arrives on /api/ui/stream. */
export function useSchedulesApi() {
  const { getApiUrl } = useConfig();

  const getSchedules = useCallback(
    () => requestAcknowledged(getApiUrl('schedules'), { requestId: crypto.randomUUID() }),
    [getApiUrl],
  );

  const retriggerSchedule = useCallback(
    (scheduleId, text) => requestAcknowledged(
      `${getApiUrl('schedules')}/${encodeURIComponent(scheduleId)}/trigger`,
      { method: 'POST', body: { text }, requestId: crypto.randomUUID() },
    ),
    [getApiUrl],
  );

  return { getSchedules, retriggerSchedule };
}
