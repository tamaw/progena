import { useCallback } from 'react';
import { useConfig } from '../../context/ConfigContext';
import { requestAcknowledged } from '../../store/http';
import { createUuid } from '../../lib/uuid';

/** Schedule operations acknowledge immediately; state arrives on /api/ui/stream. */
export function useSchedulesApi() {
  const { getApiUrl } = useConfig();

  const getSchedules = useCallback(
    () => requestAcknowledged(getApiUrl('schedules'), { requestId: createUuid() }),
    [getApiUrl],
  );

  const retriggerSchedule = useCallback(
    (scheduleId, text) => requestAcknowledged(
      `${getApiUrl('schedules')}/${encodeURIComponent(scheduleId)}/trigger`,
      { method: 'POST', body: { text }, requestId: createUuid() },
    ),
    [getApiUrl],
  );

  return { getSchedules, retriggerSchedule };
}
