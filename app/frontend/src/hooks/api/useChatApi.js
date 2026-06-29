import { useCallback, useRef } from 'react';
import { useApiClient } from './useApiClient';

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

/**
 * Chat session + streaming responses (Conversation page, runtime shell).
 *
 * This is the genuinely imperative slice of the old `useApi` god hook: it owns
 * the foreground stream lifecycle (abort/cancel) and exposes the shared
 * connection-state indicator.
 */
export function useChatApi() {
  const { config, getApiUrl, isHealthy, connectionState, setConnectionState, request } =
    useApiClient();
  const abortControllerRef = useRef(null);
  const activeStreamRequestIdRef = useRef(null);
  // Identifies which chat the foreground stream belongs to, so a new send only
  // supersedes (aborts) a stream for the SAME chat — a different chat's stream
  // is left running to finish in the background.
  const activeStreamKeyRef = useRef(null);

  const getSession = useCallback(() => request('session'), [request]);

  const createSession = useCallback(() => request('session', { method: 'POST' }), [request]);

  const sendMessageStream = useCallback(
    async (message, history = [], onChunk, onComplete, onError, onStreamEvent, metadata = null, streamKey = null) => {
      // Only abort an in-flight stream that belongs to the same chat. Sending in
      // a different chat leaves the previous chat's stream running so it can
      // finish in the background rather than being silently discarded.
      if (abortControllerRef.current && activeStreamKeyRef.current === streamKey) {
        abortControllerRef.current.abort();
      }

      const controller = new AbortController();
      abortControllerRef.current = controller;
      activeStreamKeyRef.current = streamKey;
      const requestId = `req-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
      activeStreamRequestIdRef.current = requestId;
      const input = buildResponsesInput(history, message);
      const url = getApiUrl('responses');

      try {
        setConnectionState((current) => (isHealthy ? current : 'connecting'));
        const body = { model: config.chat.defaultModel, input, stream: true };
        if (metadata && typeof metadata === 'object') {
          body.metadata = metadata;
        }
        const response = await fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Request-ID': requestId,
          },
          credentials: 'include',
          body: JSON.stringify(body),
          signal: controller.signal,
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
                onError?.(new Error(errorMessage));
                return;
              }

              if (parsed.object === 'response.stream_event') {
                onStreamEvent?.(parsed);
                continue;
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
        onComplete?.();
      } catch (error) {
        if (error.name === 'AbortError') {
          onComplete?.('cancelled');
        } else {
          setConnectionState('error');
          onError?.(error);
        }
      } finally {
        if (abortControllerRef.current === controller) {
          abortControllerRef.current = null;
          activeStreamKeyRef.current = null;
        }
        if (activeStreamRequestIdRef.current === requestId) {
          activeStreamRequestIdRef.current = null;
        }
      }
    },
    [config, getApiUrl, isHealthy, setConnectionState],
  );

  const cancelStream = useCallback(async () => {
    const requestId = activeStreamRequestIdRef.current;
    let cancelPromise = Promise.resolve();
    if (requestId) {
      const responsesUrl = getApiUrl('responses').replace(/\/$/, '');
      cancelPromise = fetch(`${responsesUrl}/${encodeURIComponent(requestId)}/cancel`, {
        method: 'PUT',
        credentials: 'include',
      })
        .catch((error) => {
          console.warn('Failed to cancel backend response stream', error);
        });
    }

    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    activeStreamRequestIdRef.current = null;
    activeStreamKeyRef.current = null;
    await cancelPromise;
  }, [getApiUrl]);

  return { connectionState, getSession, createSession, sendMessageStream, cancelStream };
}
