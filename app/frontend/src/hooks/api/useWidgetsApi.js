import { useCallback } from 'react';
import { useApiClient } from './useApiClient';

/** Widget CRUD + refresh (Tools, Workspace, Schedules pages). */
export function useWidgetsApi() {
  const { request } = useApiClient();

  const getWidgets = useCallback(() => request('widgets'), [request]);

  const getWidgetSchemas = useCallback(() => request('widgetSchemas'), [request]);

  const refreshWidget = useCallback(
    (widgetId) => request(`/api/ui/widgets/${widgetId}/refresh`, { method: 'POST' }),
    [request],
  );

  const updateWidget = useCallback(
    (widgetId, updates) => request(`/api/ui/widgets/${widgetId}`, {
      method: 'PATCH',
      body: JSON.stringify(updates),
    }),
    [request],
  );

  const deleteWidget = useCallback(
    (widgetId) => request(`/api/ui/widgets/${widgetId}`, { method: 'DELETE' }),
    [request],
  );

  return { getWidgets, getWidgetSchemas, refreshWidget, updateWidget, deleteWidget };
}
