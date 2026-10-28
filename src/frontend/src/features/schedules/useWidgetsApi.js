import { useCallback } from 'react';
import { useDispatch } from 'react-redux';
import { useConfig } from '../../context/ConfigContext';
import { refreshWidget as refresh, updateWidget as update, deleteWidget as remove } from './widgetsState';

/** Bind widget operations to the active workspace and shared request lifecycle. */
export function useWidgetsApi() {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const refreshWidget = useCallback(
    (widgetId) => dispatch(refresh({ widgetsUrl: getApiUrl('widgets'), widgetId })).unwrap(),
    [dispatch, getApiUrl],
  );
  const updateWidget = useCallback(
    (widgetId, updates, { requestId } = {}) => dispatch(update({
      widgetsUrl: getApiUrl('widgets'), widgetId, updates, requestId,
    })).unwrap(),
    [dispatch, getApiUrl],
  );
  const deleteWidget = useCallback(
    (widgetId) => dispatch(remove({ widgetsUrl: getApiUrl('widgets'), widgetId })).unwrap(),
    [dispatch, getApiUrl],
  );
  return { refreshWidget, updateWidget, deleteWidget };
}
