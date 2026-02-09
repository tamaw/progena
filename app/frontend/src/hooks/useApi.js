import { useState, useCallback, useRef } from 'react';
import { useConfig } from '../context/ConfigContext';

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
    const messages = [...history, { role: 'user', content: message }];
    const response = await fetchWithRetry('chat', {
      method: 'POST',
      body: JSON.stringify({
        model: config.chat.defaultModel,
        messages,
        stream: false
      })
    });
    return response.json();
  }, [config, fetchWithRetry]);

  const sendMessageStream = useCallback(async (message, history = [], onChunk, onComplete, onError) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    abortControllerRef.current = new AbortController();
    const messages = [...history, { role: 'user', content: message }];
    const url = getApiUrl('chat');

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: config.chat.defaultModel,
          messages,
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

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6).trim();

            if (data === '[DONE]') {
              onComplete?.();
              return;
            }

            try {
              const parsed = JSON.parse(data);
              if (parsed.error) {
                onError?.(new Error(parsed.error));
                return;
              }
              if (parsed.choices?.[0]?.delta?.content) {
                onChunk?.(parsed.choices[0].delta.content);
              }
              if (parsed.choices?.[0]?.finish_reason === 'stop') {
                onComplete?.();
                return;
              }
            } catch (e) {
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
        onError?.(error);
      }
    }
  }, [config, getApiUrl]);

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
    sendMessage,
    sendMessageStream,
    cancelStream,
    isStreaming,
    fetchWithRetry
  };
}
