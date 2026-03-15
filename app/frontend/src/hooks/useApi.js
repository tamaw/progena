import { useState, useCallback, useRef, useEffect } from 'react';
import { useConfig } from '../context/ConfigContext';
import { useHealth } from '../context/HealthContext';

function normalizeInputContent(content) {
  if (typeof content === 'string') {
    return content;
  }
  if (content === null || content === undefined) {
    return '';
  }
  if (typeof content === 'number' || typeof content === 'boolean') {
    return String(content);
  }
  try {
    return JSON.stringify(content);
  } catch {
    return String(content);
  }
}

function buildResponsesInput(history, message) {
  return history
    .concat([{ role: 'user', content: message }])
    .map((entry) => ({
      role: entry.role,
      content: normalizeInputContent(entry.content),
    }))
    .filter((entry) => entry.content);
}

export function useApi() {
  const { config, getApiUrl } = useConfig();
  const { isHealthy } = useHealth();
  const [connectionState, setConnectionState] = useState('disconnected');
  const abortControllerRef = useRef(null);

  useEffect(() => {
    setConnectionState((current) => {
      if (isHealthy) {
        return current === 'connecting' ? current : 'connected';
      }
      return current === 'connecting' ? current : 'disconnected';
    });
  }, [isHealthy]);

  const fetchWithRetry = useCallback(async (endpoint, options = {}) => {
    const url = getApiUrl(endpoint);
    const defaultOptions = {
      headers: { 'Content-Type': 'application/json' }
    };

    const mergedOptions = {
      ...defaultOptions,
      ...options,
      headers: { ...defaultOptions.headers, ...options.headers }
    };

    for (let attempt = 0; attempt < config.api.retryAttempts; attempt++) {
      try {
        setConnectionState((current) => (isHealthy ? current : 'connecting'));
        const response = await fetch(url, mergedOptions);

        if (!response.ok) {
          const error = await response.json().catch(() => ({ error: { message: response.statusText } }));
          throw new Error(error.error?.message || `HTTP ${response.status}: ${response.statusText}`);
        }

        setConnectionState('connected');
        return response;
      } catch (error) {
        if (error.name === 'AbortError') throw error;

        setConnectionState('error');
        if (attempt < config.api.retryAttempts - 1) {
          await new Promise(r => setTimeout(r, config.api.retryDelay * Math.pow(2, attempt)));
        } else {
          throw error;
        }
      }
    }
  }, [config, getApiUrl, isHealthy]);

  const getHealth = useCallback(async () => {
    const response = await fetchWithRetry('health');
    return response.json();
  }, [fetchWithRetry]);

  const getHealthDetailed = useCallback(async () => {
    const response = await fetchWithRetry('healthDetailed');
    return response.json();
  }, [fetchWithRetry]);

  const getWorkspace = useCallback(async () => {
    const response = await fetchWithRetry('workspace');
    return response.json();
  }, [fetchWithRetry]);

  const getWidgets = useCallback(async () => {
    const response = await fetchWithRetry('widgets');
    return response.json();
  }, [fetchWithRetry]);

  const getPrompts = useCallback(async () => {
    const response = await fetchWithRetry('prompts');
    return response.json();
  }, [fetchWithRetry]);

  const getPromptContent = useCallback(async (path) => {
    const search = new URLSearchParams({ path });
    const response = await fetchWithRetry(`/api/ui/prompts/content?${search.toString()}`);
    return response.json();
  }, [fetchWithRetry]);

  const updatePromptContent = useCallback(async (path, content) => {
    const response = await fetchWithRetry('/api/ui/prompts/content', {
      method: 'PUT',
      body: JSON.stringify({ path, content }),
    });
    return response.json();
  }, [fetchWithRetry]);

  const updateWidget = useCallback(async (widgetId, updates) => {
    const response = await fetchWithRetry(`/api/ui/widgets/${widgetId}`, {
      method: 'PATCH',
      body: JSON.stringify(updates),
    });
    return response.json();
  }, [fetchWithRetry]);

  const getSchedules = useCallback(async () => {
    const response = await fetchWithRetry('schedules');
    return response.json();
  }, [fetchWithRetry]);

  const getLogs = useCallback(async (params = {}) => {
    const searchParams = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        searchParams.set(key, String(value));
      }
    });
    const query = searchParams.size > 0 ? `?${searchParams.toString()}` : '';
    const response = await fetchWithRetry(`/api/ui/logs${query}`);
    return response.json();
  }, [fetchWithRetry]);

  const runAction = useCallback(async (payload) => {
    const response = await fetchWithRetry('actionRun', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    return response.json();
  }, [fetchWithRetry]);

  const generateAction = useCallback(async (payload) => {
    const response = await fetchWithRetry('actionGenerate', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    return response.json();
  }, [fetchWithRetry]);

  const getSession = useCallback(async () => {
    const response = await fetchWithRetry('session');
    return response.json();
  }, [fetchWithRetry]);

  const createSession = useCallback(async () => {
    const response = await fetchWithRetry('session', {
      method: 'POST',
    });
    return response.json();
  }, [fetchWithRetry]);

  const sendMessage = useCallback(async (message, history = []) => {
    const input = buildResponsesInput(history, message);
    const response = await fetchWithRetry('responses', {
      method: 'POST',
      body: JSON.stringify({
        model: config.chat.defaultModel,
        input,
        stream: false
      })
    });
    const data = await response.json();
    return data.output?.[0]?.content?.[0]?.text || '';
  }, [config, fetchWithRetry]);

  const sendMessageStream = useCallback(async (message, history = [], onChunk, onComplete, onError) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    abortControllerRef.current = new AbortController();
    const input = buildResponsesInput(history, message);
    const url = getApiUrl('responses');

    try {
      setConnectionState((current) => (isHealthy ? current : 'connecting'));
      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: config.chat.defaultModel,
          input,
          stream: true
        }),
        signal: abortControllerRef.current.signal
      });

      if (!response.ok) {
        const error = await response.json().catch(() => ({ error: { message: response.statusText } }));
        throw new Error(error.error?.message || `HTTP ${response.status}`);
      }

      setConnectionState('connected');

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6).trim();
            try {
              const parsed = JSON.parse(data);
              if (line.startsWith('event: error') || parsed.error) {
                onError?.(new Error(parsed.error));
                return;
              }
              const text = parsed.delta?.text || parsed.output?.[0]?.content?.[0]?.text;
              if (text) {
                onChunk?.(text);
              }
              if (parsed.object === 'response.completed') {
                onComplete?.();
                return;
              }
            } catch {
              // Ignore parse errors
            }
          }
        }
      }
      onComplete?.();
    } catch (error) {
      if (error.name === 'AbortError') {
        onComplete?.('cancelled');
      } else {
        setConnectionState('error');
        onError?.(error);
      }
    }
  }, [config, getApiUrl, isHealthy]);

  const cancelStream = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
  }, []);

  const isStreaming = useCallback(() => {
    return abortControllerRef.current !== null;
  }, []);

  return {
    connectionState,
    getHealth,
    getHealthDetailed,
    getWorkspace,
    getWidgets,
    getPrompts,
    getPromptContent,
    updatePromptContent,
    updateWidget,
    getSchedules,
    getLogs,
    runAction,
    generateAction,
    getSession,
    createSession,
    sendMessage,
    sendMessageStream,
    cancelStream,
    isStreaming,
    fetchWithRetry
  };
}
