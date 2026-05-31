/* eslint-disable react-refresh/only-export-components */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useApi } from '../hooks/useApi';

const AlertCenterContext = createContext(null);

function sortAlerts(alerts = []) {
  return [...alerts].sort((left, right) => (right.timestamp || 0) - (left.timestamp || 0));
}

export function AlertCenterProvider({ children }) {
  const { getAlerts, markAlertsViewed } = useApi();
  const [alerts, setAlerts] = useState([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [isPanelOpen, setIsPanelOpen] = useState(false);
  const [selectedAlertId, setSelectedAlertId] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');
  const mountedRef = useRef(false);

  const applyAlertPayload = useCallback((payload) => {
    const nextAlerts = sortAlerts(payload?.alerts || []);
    setAlerts(nextAlerts);
    setUnreadCount(
      typeof payload?.unread === 'number'
        ? payload.unread
        : nextAlerts.filter((alert) => !alert.viewed).length
    );
  }, []);

  const loadAlerts = useCallback(async ({ background = false } = {}) => {
    if (!background) {
      setIsLoading(true);
    }

    try {
      const payload = await getAlerts();
      if (!mountedRef.current) {
        return;
      }
      applyAlertPayload(payload);
      setError('');
    } catch (loadError) {
      if (!mountedRef.current) {
        return;
      }
      setError(loadError.message);
    } finally {
      if (mountedRef.current && !background) {
        setIsLoading(false);
      }
    }
  }, [applyAlertPayload, getAlerts]);

  useEffect(() => {
    mountedRef.current = true;

    return () => {
      mountedRef.current = false;
    };
  }, []);

  const ingestStatusEvent = useCallback((event) => {
    if (event?.type === 'alerts' && event.data) {
      applyAlertPayload(event.data);
      setIsLoading(false);
      setError('');
      return;
    }

    if (event?.type === 'shutdown') {
      setIsPanelOpen(false);
      setSelectedAlertId(null);
    }
  }, [applyAlertPayload]);

  const markAllViewed = useCallback(async () => {
    if (unreadCount <= 0 && !alerts.some((alert) => !alert.viewed)) {
      return;
    }

    try {
      await markAlertsViewed();
      setAlerts((currentAlerts) => currentAlerts.map((alert) => ({ ...alert, viewed: true })));
      setUnreadCount(0);
      setError('');
    } catch (markError) {
      if (mountedRef.current) {
        setError(markError.message);
      }
    }
  }, [alerts, markAlertsViewed, unreadCount]);

  const openPanel = useCallback(async () => {
    setIsPanelOpen(true);
    setSelectedAlertId(null);
    await markAllViewed();
  }, [markAllViewed]);

  const closePanel = useCallback(() => {
    setIsPanelOpen(false);
    setSelectedAlertId(null);
  }, []);

  const openAlert = useCallback((alertId) => {
    setSelectedAlertId(alertId);
  }, []);

  const closeAlert = useCallback(() => {
    setSelectedAlertId(null);
  }, []);

  const selectedAlert = useMemo(
    () => alerts.find((alert) => alert.id === selectedAlertId) || null,
    [alerts, selectedAlertId]
  );

  const value = useMemo(() => ({
    alerts,
    unreadCount,
    hasUnread: unreadCount > 0,
    isPanelOpen,
    selectedAlert,
    isLoading,
    error,
    openPanel,
    closePanel,
    openAlert,
    closeAlert,
    markAllViewed,
    ingestStatusEvent,
    refreshAlerts: loadAlerts,
  }), [
    alerts,
    closeAlert,
    closePanel,
    error,
    isLoading,
    isPanelOpen,
    ingestStatusEvent,
    loadAlerts,
    markAllViewed,
    openAlert,
    openPanel,
    selectedAlert,
    unreadCount,
  ]);

  return (
    <AlertCenterContext.Provider value={value}>
      {children}
    </AlertCenterContext.Provider>
  );
}

export function useAlertCenter() {
  const context = useContext(AlertCenterContext);
  if (!context) {
    throw new Error('useAlertCenter must be used within an AlertCenterProvider');
  }
  return context;
}
