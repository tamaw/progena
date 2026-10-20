import { useCallback } from 'react';
import { useConfig } from '../../context/ConfigContext';
import { createOperationRequestId, requestAcknowledged } from '../../store/http';
import { createUuid } from '../../lib/uuid';

/** Schedule operations acknowledge immediately; state arrives on /api/ui/stream. */
export function useSchedulesApi() {
  const { getApiUrl } = useConfig();

  const getSchedules = useCallback(
    () => requestAcknowledged(getApiUrl('schedules'), { requestId: createUuid() }),
    [getApiUrl],
  );

  // Draft saves pass their own requestId so the save is correlated (and
  // failed by a disconnect) before the HTTP acknowledgement arrives.
  const retriggerSchedule = useCallback(
    (scheduleId, text, { requestId } = {}) => requestAcknowledged(
      `${getApiUrl('schedules')}/${encodeURIComponent(scheduleId)}/trigger`,
      { method: 'POST', body: { text }, requestId: createOperationRequestId({ requestId }) },
    ),
    [getApiUrl],
  );

  return { getSchedules, retriggerSchedule };
}
