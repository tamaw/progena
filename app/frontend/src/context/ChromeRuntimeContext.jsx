/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useAlertCenter } from './AlertCenterContext';
import { useHealth } from './HealthContext';
import { useConfig } from './ConfigContext';
import { useStatusSSE } from '../hooks/useSSE';
import { fetchWidgets, selectUiStatus, selectUiWidgets } from '../store/uiDataSlice';

const ChromeRuntimeContext = createContext(null);

export function ChromeRuntimeProvider({ children }) {
  const health = useHealth();
  const { ingestStatusEvent } = useAlertCenter();
  const { getApiUrl } = useConfig();
  const dispatch = useDispatch();
  // Widgets and status are owned by the Redux realtime store (fed by
  // `widgets.updated` / `status.updated` on /api/ui/stream, which merges partial
  // pushes). ChromeRuntime no longer keeps its own copies — it just re-exposes
  // the single source of truth.
  const widgets = useSelector(selectUiWidgets);
  const status = useSelector(selectUiStatus);
  const [lastEventAt, setLastEventAt] = useState(null);

  const widgetsUrl = getApiUrl('widgets');
  const refreshWidgets = useCallback(
    () => dispatch(fetchWidgets({ widgetsUrl })),
    [dispatch, widgetsUrl],
  );

  // Seed the widget list once on mount; thereafter it stays live via the stream.
  useEffect(() => {
    refreshWidgets();
  }, [refreshWidgets]);

  const handleStatusEvent = useCallback((event) => {
    // Status itself now lives in Redux; this stream is kept only for alert
    // ingestion and a liveness timestamp.
    ingestStatusEvent(event);
    if (event?.data) {
      setLastEventAt(Date.now());
    }
  }, [ingestStatusEvent]);

  const { isConnected } = useStatusSSE(handleStatusEvent);

  const value = useMemo(() => ({
    health,
    status,
    widgets,
    isStatusStreamConnected: isConnected,
    lastEventAt,
    error: '',
    refreshWidgets,
  }), [health, isConnected, lastEventAt, refreshWidgets, status, widgets]);

  return (
    <ChromeRuntimeContext.Provider value={value}>
      {children}
    </ChromeRuntimeContext.Provider>
  );
}

export function useChromeRuntime() {
  const context = useContext(ChromeRuntimeContext);
  if (!context) {
    throw new Error('useChromeRuntime must be used within a ChromeRuntimeProvider');
  }
  return context;
}
