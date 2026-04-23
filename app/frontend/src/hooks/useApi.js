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

function parseSSEEventBlock(block) {
  if (!block) {
    return null;
  }

  let eventType = 'message';
  const dataLines = [];

  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) {
      eventType = line.slice(6).trim() || 'message';
      continue;
    }
    if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart());
    }
  }

  if (dataLines.length === 0) {
    return null;
  }

  return {
    eventType,
    data: dataLines.join('\n'),
  };
}

export function useApi() {
  const { config, getApiUrl } = useConfig();
  const { isHealthy } = useHealth();
  const [connectionState, setConnectionState] = useState('disconnected');
  const abortControllerRef = useRef(null);
  const mountedRef = useRef(true);

  const setConnectionStateIfMounted = useCallback((value) => {
    if (mountedRef.current) {
      setConnectionState(value);
    }
  }, []);

  const canInvokeCallback = useCallback((signal) => (
    mountedRef.current && !(signal?.aborted)
  ), []);

  useEffect(() => {
    setConnectionStateIfMounted((current) => {
      if (isHealthy) {
        return current === 'connecting' ? current : 'connected';
      }
      return current === 'connecting' ? current : 'disconnected';
    });
  }, [isHealthy, setConnectionStateIfMounted]);

  useEffect(() => () => {
    mountedRef.current = false;
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
  }, []);

  const fetchWithRetry = useCallback(async (endpoint, options = {}) => {
    const url = getApiUrl(endpoint);
    const defaultOptions = {
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include'
    };

    const mergedOptions = {
      ...defaultOptions,
      ...options,
      headers: { ...defaultOptions.headers, ...options.headers }
      };

    for (let attempt = 0; attempt < config.api.retryAttempts; attempt++) {
      try {
        setConnectionStateIfMounted((current) => (isHealthy ? current : 'connecting'));
        const response = await fetch(url, mergedOptions);

        if (!response.ok) {
          const error = await response.json().catch(() => ({}));
          const message = error.error?.message
            || error.detail
            || error.message
            || `HTTP ${response.status}: ${response.statusText}`;
          throw new Error(typeof message === 'string' ? message : JSON.stringify(message));
        }

        setConnectionStateIfMounted('connected');
        return response;
      } catch (error) {
        if (error.name === 'AbortError') throw error;

        setConnectionStateIfMounted('error');
        if (attempt < config.api.retryAttempts - 1) {
          await new Promise(r => setTimeout(r, config.api.retryDelay * Math.pow(2, attempt)));
        } else {
          throw error;
        }
      }
    }
  }, [config, getApiUrl, isHealthy, setConnectionStateIfMounted]);

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

  const getWidgetSchemas = useCallback(async () => {
    const response = await fetchWithRetry('widgetSchemas');
    return response.json();
  }, [fetchWithRetry]);

  const refreshWidget = useCallback(async (widgetId) => {
    const response = await fetchWithRetry(`/api/ui/widgets/${widgetId}/refresh`, {
      method: 'POST',
    });
    return response.json();
  }, [fetchWithRetry]);

  const getKanbanBoard = useCallback(async () => {
    const response = await fetchWithRetry('kanban');
    return response.json();
  }, [fetchWithRetry]);

  const createKanbanCard = useCallback(async (payload) => {
    const response = await fetchWithRetry('/api/ui/kanban/cards', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    return response.json();
  }, [fetchWithRetry]);

  const updateKanbanCard = useCallback(async (cardId, payload) => {
    const response = await fetchWithRetry(`/api/ui/kanban/cards/${cardId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
    return response.json();
  }, [fetchWithRetry]);

  const deleteKanbanCard = useCallback(async (cardId) => {
    await fetchWithRetry(`/api/ui/kanban/cards/${cardId}`, {
      method: 'DELETE',
    });
  }, [fetchWithRetry]);

  const getPrompts = useCallback(async (agent = null) => {
    const search = agent ? `?${new URLSearchParams({ agent }).toString()}` : '';
    const response = await fetchWithRetry(`prompts${search}`);
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

  const deleteWidget = useCallback(async (widgetId) => {
    const response = await fetchWithRetry(`/api/ui/widgets/${widgetId}`, {
      method: 'DELETE',
    });
    return response.json();
  }, [fetchWithRetry]);

  const getSchedules = useCallback(async () => {
    const response = await fetchWithRetry('schedules');
    return response.json();
  }, [fetchWithRetry]);

  const createSchedule = useCallback(async (payload) => {
    const response = await fetchWithRetry('schedules', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    return response.json();
  }, [fetchWithRetry]);

  const createEvent = useCallback(async (payload) => {
    const response = await fetchWithRetry('events', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
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

  const getLogSources = useCallback(async () => {
    const response = await fetchWithRetry('/api/ui/logs/sources');
    return response.json();
  }, [fetchWithRetry]);

  const getAlerts = useCallback(async () => {
    const response = await fetchWithRetry('/api/ui/alerts');
    return response.json();
  }, [fetchWithRetry]);

  const markAlertsViewed = useCallback(async (alertIds = null) => {
    const response = await fetchWithRetry('/api/ui/alerts/viewed', {
      method: 'POST',
      body: alertIds ? JSON.stringify({ alert_ids: alertIds }) : undefined,
    });
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

  const sendMessageStream = useCallback(
    async (message, history = [], onChunk, onComplete, onError, onStreamEvent) => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }

      const controller = new AbortController();
      abortControllerRef.current = controller;
      const input = buildResponsesInput(history, message);
      const url = getApiUrl('responses');

      try {
        setConnectionStateIfMounted((current) => (isHealthy ? current : 'connecting'));
        const response = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({
            model: config.chat.defaultModel,
            input,
            stream: true
          }),
          signal: controller.signal
        });

        if (!response.ok) {
          const error = await response.json().catch(() => ({ error: { message: response.statusText } }));
          throw new Error(error.error?.message || `HTTP ${response.status}`);
        }

        setConnectionStateIfMounted('connected');

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
          const blocks = buffer.split('\n\n');
          buffer = blocks.pop() || '';

          for (const block of blocks) {
            const parsedEvent = parseSSEEventBlock(block.trim());
            if (!parsedEvent) {
              continue;
            }

            try {
              const parsed = JSON.parse(parsedEvent.data);
              if (parsedEvent.eventType === 'error' || parsed.error) {
                const errorMessage = parsed.error?.message || parsed.error || 'Streaming error';
                if (canInvokeCallback(controller.signal)) {
                  onError?.(new Error(errorMessage));
                }
                return;
              }

              if (parsed.object === 'response.stream_event') {
                if (canInvokeCallback(controller.signal)) {
                  onStreamEvent?.(parsed);
                }
                continue;
              }

              const text = parsed.delta?.text || parsed.output?.[0]?.content?.[0]?.text;
              if (text && canInvokeCallback(controller.signal)) {
                onChunk?.(text);
              }
              if (parsed.object === 'response.completed') {
                if (canInvokeCallback(controller.signal)) {
                  onComplete?.();
                }
                return;
              }
            } catch {
              // Ignore parse errors
            }
          }
        }
        if (canInvokeCallback(controller.signal)) {
          onComplete?.();
        }
      } catch (error) {
        if (error.name === 'AbortError') {
          if (mountedRef.current) {
            onComplete?.('cancelled');
          }
        } else {
          setConnectionStateIfMounted('error');
          if (canInvokeCallback(controller.signal)) {
            onError?.(error);
          }
        }
      } finally {
        if (abortControllerRef.current === controller) {
          abortControllerRef.current = null;
        }
      }
    },
    [canInvokeCallback, config, getApiUrl, isHealthy, setConnectionStateIfMounted]
  );

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
    getWidgetSchemas,
    refreshWidget,
    getKanbanBoard,
    createKanbanCard,
    updateKanbanCard,
    deleteKanbanCard,
    getPrompts,
    getPromptContent,
    updatePromptContent,
    updateWidget,
    deleteWidget,
    getSchedules,
    createSchedule,
    createEvent,
    getLogs,
    getLogSources,
    getAlerts,
    markAlertsViewed,
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
