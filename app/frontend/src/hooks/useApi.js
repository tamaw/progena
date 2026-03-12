import { useState, useCallback, useRef } from 'react';
import { useConfig } from '../hooks/useConfig';

export function useApi() {
  const { config, getApiUrl } = useConfig();
  const [connectionState, setConnectionState] = useState('disconnected');
  const abortControllerRef = useRef(null);

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
        setConnectionState('connecting');
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
  }, [config, getApiUrl]);

  const getHealth = useCallback(async () => {
    const response = await fetchWithRetry('health');
    return response.json();
  }, [fetchWithRetry]);

  const getHealthDetailed = useCallback(async () => {
    const response = await fetchWithRetry('healthDetailed');
    return response.json();
  }, [fetchWithRetry]);

  const sendMessage = useCallback(async (message, history = []) => {
    const input = [
      ...history.map((entry) => ({
        role: entry.role,
        content: entry.content
      })),
      {
        role: 'user',
        content: message
      }
    ];
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
    const input = [
      ...history.map((entry) => ({
        role: entry.role,
        content: entry.content
      })),
      {
        role: 'user',
        content: message
      }
    ];
    const url = getApiUrl('responses');

    try {
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

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let eventType = 'message';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const messages = buffer.split('\n\n');
        buffer = messages.pop() || '';

        for (const messageBlock of messages) {
          const lines = messageBlock
            .split('\n')
            .map((line) => line.trim())
            .filter(Boolean);

          for (const line of lines) {
            if (line.startsWith('event: ')) {
              eventType = line.slice(7).trim();
              continue;
            }

            if (!line.startsWith('data: ')) {
              continue;
            }

            const rawData = line.slice(6).trim();
            if (!rawData) {
              continue;
            }

            try {
              const parsed = JSON.parse(rawData);

              if (eventType === 'error' || parsed.error) {
                const messageText = typeof parsed.error === 'string'
                  ? parsed.error
                  : parsed.error?.message || 'Unknown streaming error';
                onError?.(new Error(messageText));
                return;
              }

              if (parsed.object === 'response.output_text.delta' && parsed.delta?.text) {
                onChunk?.(parsed.delta.text);
              }

              if (parsed.object === 'response.completed' || eventType === 'response.completed') {
                onComplete?.();
                return;
              }
            } catch {
              // Ignore parse errors from keepalive or partial events.
            }
          }
        }
      }
      onComplete?.();
    } catch (error) {
      if (error.name === 'AbortError') {
        onComplete?.('cancelled');
      } else {
        onError?.(error);
      }
    }
  }, [config, getApiUrl]);

  const cancelStream = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
  }, []);

  const isStreaming = useCallback(() => {
    return abortControllerRef.current !== null;
  }, []);

  return {
    connectionState,
    getHealth,
    getHealthDetailed,
    sendMessage,
    sendMessageStream,
    cancelStream,
    isStreaming,
    fetchWithRetry
  };
}
