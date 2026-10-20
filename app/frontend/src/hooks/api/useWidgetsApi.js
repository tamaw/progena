import { useCallback } from 'react';
import { useConfig } from '../../context/ConfigContext';
import { createOperationRequestId, requestAcknowledged } from '../../store/http';
import { createUuid } from '../../lib/uuid';

/** Widget operations acknowledge immediately; state arrives on /api/ui/stream. */
export function useWidgetsApi() {
  const { getApiUrl } = useConfig();

  const getWidgets = useCallback(
    () => requestAcknowledged(getApiUrl('widgets'), { requestId: createUuid() }),
    [getApiUrl],
  );


  const refreshWidget = useCallback(
    (widgetId) => requestAcknowledged(
      `${getApiUrl('widgets')}/${encodeURIComponent(widgetId)}/refresh`,
      { method: 'POST', requestId: createUuid() },
    ),
    [getApiUrl],
  );

  // Draft saves pass their own requestId so the save is correlated (and
  // failed by a disconnect) before the HTTP acknowledgement arrives.
  const updateWidget = useCallback(
    (widgetId, updates, { requestId } = {}) => requestAcknowledged(
      `${getApiUrl('widgets')}/${encodeURIComponent(widgetId)}`,
      { method: 'PATCH', body: updates, requestId: createOperationRequestId({ requestId }) },
    ),
    [getApiUrl],
  );

  const deleteWidget = useCallback(
    (widgetId) => requestAcknowledged(
      `${getApiUrl('widgets')}/${encodeURIComponent(widgetId)}`,
      { method: 'DELETE', requestId: createUuid() },
    ),
    [getApiUrl],
  );

  return { getWidgets, refreshWidget, updateWidget, deleteWidget };
}
