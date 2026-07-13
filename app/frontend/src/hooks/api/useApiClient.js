import { useState, useCallback, useEffect } from 'react';
import { useConfig } from '../../context/ConfigContext';

/**
 * Core API client shared by every domain hook under `hooks/api/`.
 *
 * Owns the connection-state machine and the retrying fetch. Domain hooks build
 * thin, endpoint-specific wrappers on top of `request` (JSON helper) or
 * `fetchWithRetry` (raw Response) — see useWidgetsApi, useChatApi, etc.
 */
export function useApiClient() {
  const { config, getApiUrl } = useConfig();
  const [connectionState, setConnectionState] = useState('disconnected');

  useEffect(() => {
    setConnectionState((current) => {
      return current === 'connecting' ? current : 'connected';
    });
  }, []);

  const fetchWithRetry = useCallback(async (endpoint, options = {}) => {
    const url = getApiUrl(endpoint);
    const defaultOptions = {
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
    };

    const mergedOptions = {
      ...defaultOptions,
      ...options,
      headers: { ...defaultOptions.headers, ...options.headers },
    };

    for (let attempt = 0; attempt < config.api.retryAttempts; attempt++) {
      try {
        setConnectionState('connecting');
        const response = await fetch(url, mergedOptions);

        if (!response.ok) {
          const error = await response.json().catch(() => ({}));
          const message = error.error?.message
            || error.detail
            || error.message
            || `HTTP ${response.status}: ${response.statusText}`;
          throw new Error(typeof message === 'string' ? message : JSON.stringify(message));
        }

        setConnectionState('connected');
        return response;
      } catch (error) {
        if (error.name === 'AbortError') throw error;

        setConnectionState('error');
        if (attempt < config.api.retryAttempts - 1) {
          await new Promise((r) => setTimeout(r, config.api.retryDelay * Math.pow(2, attempt)));
        } else {
          throw error;
        }
      }
    }
    return undefined;
  }, [config, getApiUrl]);

  // Fetch + parse JSON, tolerating empty (204/no-body) responses.
  const request = useCallback(async (endpoint, options) => {
    const response = await fetchWithRetry(endpoint, options);
    const text = await response.text();
    if (!text) {
      return null;
    }
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }, [fetchWithRetry]);

  return {
    config,
    getApiUrl,
    isHealthy: true,
    connectionState,
    setConnectionState,
    fetchWithRetry,
    request,
  };
}
