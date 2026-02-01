import { useEffect, useRef, useState, useCallback } from 'react';
import { useConfig } from '../context/ConfigContext';

export function useSSE(onEvent) {
  const { getApiUrl, config } = useConfig();
  const eventSourceRef = useRef(null);
  const reconnectAttemptRef = useRef(0);
  const [isConnected, setIsConnected] = useState(false);

  const connect = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const url = getApiUrl('events');
    eventSourceRef.current = new EventSource(url);

    eventSourceRef.current.onopen = () => {
      setIsConnected(true);
      reconnectAttemptRef.current = 0;
    };

    eventSourceRef.current.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        onEvent?.(data);
      } catch (e) {
        // Ignore parse errors
      }
    };

    eventSourceRef.current.onerror = () => {
      setIsConnected(false);
      eventSourceRef.current?.close();

      if (reconnectAttemptRef.current < config.sse.maxReconnectAttempts) {
        const delay = config.sse.reconnectDelay * Math.pow(2, reconnectAttemptRef.current);
        reconnectAttemptRef.current++;
        setTimeout(connect, delay);
      }
    };
  }, [getApiUrl, onEvent, config.sse]);

  useEffect(() => {
    connect();

    return () => {
      eventSourceRef.current?.close();
    };
  }, [connect]);

  const disconnect = useCallback(() => {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    setIsConnected(false);
  }, []);

  return { isConnected, connect, disconnect };
}
