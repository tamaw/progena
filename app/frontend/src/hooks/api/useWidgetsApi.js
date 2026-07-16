import { useCallback } from 'react';
import { useConfig } from '../../context/ConfigContext';
import { requestAcknowledged } from '../../store/http';
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

  const updateWidget = useCallback(
    (widgetId, updates) => requestAcknowledged(
      `${getApiUrl('widgets')}/${encodeURIComponent(widgetId)}`,
      { method: 'PATCH', body: updates, requestId: createUuid() },
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
