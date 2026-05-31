/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useMemo, useState } from 'react';
import { useAlertCenter } from './AlertCenterContext';
import { useHealth } from './HealthContext';
import { useApi } from '../hooks/useApi';
import { useStatusSSE } from '../hooks/useSSE';

const ChromeRuntimeContext = createContext(null);

function normalizeWidgets(payload) {
  return payload?.items || payload?.widgets || (Array.isArray(payload) ? payload : []);
}

export function ChromeRuntimeProvider({ children }) {
  const health = useHealth();
  const { ingestStatusEvent } = useAlertCenter();
  const { getWidgets } = useApi();
  const [status, setStatus] = useState(null);
  const [widgets, setWidgets] = useState([]);
  const [lastEventAt, setLastEventAt] = useState(null);
  const [error, setError] = useState('');

  const refreshWidgets = useCallback(async () => {
    try {
      const response = await getWidgets();
      setWidgets(normalizeWidgets(response));
      setError('');
    } catch (refreshError) {
      setError(refreshError.message);
    }
  }, [getWidgets]);

  const handleStatusEvent = useCallback((event) => {
    ingestStatusEvent(event);

    if (!event?.data) return;

    setLastEventAt(Date.now());
    if (event.type === 'connected' || event.type === 'status' || event.type === 'heartbeat') {
      setStatus(event.data);
      return;
    }

    if (event.type === 'widgets') {
      setWidgets(normalizeWidgets(event.data));
    }
  }, [ingestStatusEvent]);

  const { isConnected } = useStatusSSE(handleStatusEvent);

  const value = useMemo(() => ({
    health,
    status,
    widgets,
    isStatusStreamConnected: isConnected,
    lastEventAt,
    error,
    refreshWidgets,
  }), [error, health, isConnected, lastEventAt, refreshWidgets, status, widgets]);

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
