import { useCallback } from 'react';
import { useApiClient } from './useApiClient';

/** Alert center read + mark-viewed (Alert center context). */
export function useAlertsApi() {
  const { request } = useApiClient();

  const getAlerts = useCallback(() => request('/api/ui/alerts'), [request]);

  const markAlertsViewed = useCallback(
    (alertIds = null) => request('/api/ui/alerts/viewed', {
      method: 'POST',
      body: alertIds ? JSON.stringify({ alert_ids: alertIds }) : undefined,
    }),
    [request],
  );

  return { getAlerts, markAlertsViewed };
}
