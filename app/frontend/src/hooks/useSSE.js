import { useEffect, useRef, useState, useCallback } from 'react';
import { useConfig } from '../context/ConfigContext';
import { useHealth } from '../context/HealthContext';

/**
 * Build full URL for an API path.
 * Ensures proper slash handling between baseUrl and path.
 */
function buildUrl(baseUrl, path) {
  const base = baseUrl.endsWith('/') ? baseUrl.slice(0, -1) : baseUrl;
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${base}${p}`;
}

/**
 * Shared SSE connection hook.
 *
 * Opens a single EventSource to `path`, attaches a listener for each named type
 * in `eventTypes`, and forwards parsed payloads to `onEvent` as `{ type, data }`.
 * Connects while the app is healthy (via HealthContext) and tears down otherwise.
 *
 * `eventTypes` must be a stable reference (define it at module scope) so the
 * connection is not rebuilt on every render.
 */
export function useSSEConnection(path, eventTypes, onEvent) {
  const { config } = useConfig();
  const { isHealthy } = useHealth();
  const eventSourceRef = useRef(null);
  const isConnectingRef = useRef(false);
  const [isConnected, setIsConnected] = useState(false);

  const baseUrl = config.api.baseUrl;

  const cleanup = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    isConnectingRef.current = false;
  }, []);

  const connect = useCallback(() => {
    if (eventSourceRef.current || isConnectingRef.current) {
      return;
    }
    isConnectingRef.current = true;

    const url = buildUrl(baseUrl, path);
    const es = new EventSource(url);
    eventSourceRef.current = es;

    es.onopen = () => {
      setIsConnected(true);
      isConnectingRef.current = false;
    };

    const handleEvent = (type) => (event) => {
      try {
        const data = JSON.parse(event.data);
        onEvent?.({ type, data });
      } catch {
        // Ignore parse errors
      }
    };

    for (const type of eventTypes) {
      es.addEventListener(type, handleEvent(type));
    }
    es.onmessage = handleEvent('message');

    es.onerror = () => {
      setIsConnected(false);
      isConnectingRef.current = false;
      es.close();
      eventSourceRef.current = null;
    };
  }, [baseUrl, path, eventTypes, onEvent]);

  // Connect when healthy, disconnect when not
  useEffect(() => {
    if (isHealthy && !eventSourceRef.current && !isConnectingRef.current) {
      connect();
    } else if (!isHealthy && eventSourceRef.current) {
      cleanup();
      queueMicrotask(() => setIsConnected(false));
    }
  }, [isHealthy, connect, cleanup]);

  // Cleanup on unmount
  useEffect(() => {
    return () => cleanup();
  }, [cleanup]);

  const disconnect = useCallback(() => {
    cleanup();
    setIsConnected(false);
  }, [cleanup]);

  return { isConnected, isHealthy, connect, disconnect };
}

/**
 * Hook for connecting to the /status SSE stream.
 * Receives status-side events: status, heartbeat, alerts, widgets.
 * The alerts event carries the full alert-center payload.
 */
const STATUS_EVENT_TYPES = ['connected', 'status', 'heartbeat', 'alerts', 'widgets', 'shutdown'];

export function useStatusSSE(onEvent) {
  return useSSEConnection('/api/ui/status', STATUS_EVENT_TYPES, onEvent);
}
