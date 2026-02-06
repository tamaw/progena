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
 * Hook for connecting to the /events SSE stream.
 * Receives IPC events: scheduled_response, agent_message, alert, task, agent.
 *
 * Uses shared HealthContext for health status - no duplicate health checks.
 */
export function useEventsSSE(onEvent) {
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

    const url = buildUrl(baseUrl, '/api/ui/events');
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
      } catch (e) {
        // Ignore parse errors
      }
    };

    es.addEventListener('connected', handleEvent('connected'));
    es.addEventListener('heartbeat', handleEvent('heartbeat'));
    es.addEventListener('alert', handleEvent('alert'));
    es.addEventListener('task', handleEvent('task'));
    es.addEventListener('agent', handleEvent('agent'));
    es.addEventListener('shutdown', handleEvent('shutdown'));
    es.addEventListener('scheduled_response', handleEvent('scheduled_response'));
    es.addEventListener('agent_message', handleEvent('agent_message'));

    es.onmessage = handleEvent('message');

    es.onerror = (err) => {
      console.error('[useEventsSSE] Connection error:', err);
      setIsConnected(false);
      isConnectingRef.current = false;
      es.close();
      eventSourceRef.current = null;
    };
  }, [baseUrl, onEvent]);

  // Connect when healthy, disconnect when not
  useEffect(() => {
    console.log('[useEventsSSE] isHealthy:', isHealthy, 'hasES:', !!eventSourceRef.current);
    if (isHealthy && !eventSourceRef.current && !isConnectingRef.current) {
      console.log('[useEventsSSE] Connecting to /events...');
      connect();
    } else if (!isHealthy && eventSourceRef.current) {
      console.log('[useEventsSSE] Disconnecting (unhealthy)');
      cleanup();
      setIsConnected(false);
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
 * Receives status/health events: status, heartbeat.
 *
 * Uses shared HealthContext for health status - no duplicate health checks.
 */
export function useStatusSSE(onEvent) {
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

    const url = buildUrl(baseUrl, '/api/ui/status');
    console.log('[useStatusSSE] Connecting to', url);
    const es = new EventSource(url);

    es.onopen = () => {
      setIsConnected(true);
      isConnectingRef.current = false;
    };

    const handleEvent = (type) => (event) => {
      try {
        const data = JSON.parse(event.data);
        onEvent?.({ type, data });
      } catch (e) {
        // Ignore parse errors
      }
    };

    es.addEventListener('connected', handleEvent('connected'));
    es.addEventListener('status', handleEvent('status'));
    es.addEventListener('heartbeat', handleEvent('heartbeat'));
    es.addEventListener('shutdown', handleEvent('shutdown'));

    es.onmessage = handleEvent('message');

    es.onerror = () => {
      setIsConnected(false);
      isConnectingRef.current = false;
      es.close();
      eventSourceRef.current = null;
    };
  }, [baseUrl, onEvent]);

  // Connect when healthy, disconnect when not
  useEffect(() => {
    if (isHealthy && !eventSourceRef.current && !isConnectingRef.current) {
      connect();
    } else if (!isHealthy && eventSourceRef.current) {
      cleanup();
      setIsConnected(false);
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
