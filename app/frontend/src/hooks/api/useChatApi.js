import { useCallback, useEffect, useRef, useState } from 'react';
import { useConfig } from '../../context/ConfigContext';
import { subscribeUiEvent } from '../../store/realtimeEvents';
import { requestAcknowledged } from '../../store/http';

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

/**
 * Foreground Responses API lifecycle shared by the conversation page and
 * runtime shell. Dispatch receives a 202 acknowledgement; authoritative
 * generation data and terminal state arrive through the Redux UI event stream.
 */
export function useChatApi() {
  const { config, getApiUrl } = useConfig();
  const [connectionState, setConnectionState] = useState('disconnected');
  const activeStreamRequestIdRef = useRef(null);
  const activeAcknowledgementRef = useRef(null);
  const pendingStreamsRef = useRef(new Map());
  const consumeStreamEvent = useCallback((event) => {
    const type = event?.type;
    const payload = event?.payload || event?.data;
    if (typeof type !== 'string' || !type.startsWith('chat.response.') || !payload) {
      return;
    }

    const requestId = payload.request_id;
    const pending = requestId ? pendingStreamsRef.current.get(requestId) : null;
    if (!pending) {
      return;
    }

    if (type === 'chat.response.started') {
      if (!pending.cancelRequested) {
        setConnectionState('connected');
      }
      return;
    }

    if (type === 'chat.response.delta') {
      const delta = typeof payload.delta === 'string' ? payload.delta : '';
      if (delta && !pending.cancelRequested) {
        pending.onChunk?.(delta);
      }
      return;
    }

    if (type === 'chat.response.event') {
      if (!pending.cancelRequested) {
        pending.onStreamEvent?.(payload);
      }
      return;
    }

    if (!['chat.response.completed', 'chat.response.cancelled', 'chat.response.failed'].includes(type)) {
      return;
    }

    pendingStreamsRef.current.delete(requestId);
    if (activeAcknowledgementRef.current?.requestId === requestId) {
      activeAcknowledgementRef.current = null;
    }
    if (activeStreamRequestIdRef.current === requestId) {
      activeStreamRequestIdRef.current = null;
    }

    if (type === 'chat.response.failed') {
      const message = typeof payload.error === 'string' && payload.error
        ? payload.error
        : 'Response generation failed';
      pending.onError?.(new Error(message));
      setConnectionState('error');
    } else {
      pending.onComplete?.(type === 'chat.response.cancelled' ? 'cancelled' : '');
      setConnectionState('disconnected');
    }
    pending.resolve();
  }, []);

  useEffect(() => subscribeUiEvent(consumeStreamEvent), [consumeStreamEvent]);

  const sendMessageStream = useCallback(
    async (message, history = [], onChunk, onComplete, onError, onStreamEvent, metadata = null) => {
      const requestId = crypto.randomUUID();
      const input = buildResponsesInput(history, message);
      const url = getApiUrl('responses');
      let resolveTerminal;
      const terminal = new Promise((resolve) => {
        resolveTerminal = resolve;
      });
      const acknowledgementController = new AbortController();
      const pending = {
        onChunk,
        onComplete,
        onError,
        onStreamEvent,
        resolve: resolveTerminal,
        cancelRequested: false,
      };

      // Register before dispatch: the backend can broadcast `started` before
      // the 202 acknowledgement reaches the browser.
      pendingStreamsRef.current.set(requestId, pending);
      activeStreamRequestIdRef.current = requestId;
      activeAcknowledgementRef.current = { requestId, controller: acknowledgementController };
      setConnectionState('connecting');

      const body = { model: config.chat.defaultModel, input, stream: true };
      if (metadata && typeof metadata === 'object') {
        body.metadata = metadata;
      }

      try {
        await requestAcknowledged(url, {
          method: 'POST',
          body,
          requestId,
          errorLabel: 'sendMessage failed',
          signal: acknowledgementController.signal,
        });
        if (activeAcknowledgementRef.current?.requestId === requestId) {
          activeAcknowledgementRef.current = null;
        }
        if (pendingStreamsRef.current.get(requestId) === pending && !pending.cancelRequested) {
          setConnectionState('connected');
        }
      } catch (error) {
        if (activeAcknowledgementRef.current?.requestId === requestId) {
          activeAcknowledgementRef.current = null;
        }
        if (pendingStreamsRef.current.get(requestId) === pending && !pending.cancelRequested) {
          pendingStreamsRef.current.delete(requestId);
          if (activeStreamRequestIdRef.current === requestId) {
            activeStreamRequestIdRef.current = null;
          }
          setConnectionState('error');
          onError?.(error);
          resolveTerminal();
        }
      }

      // Acknowledgement only confirms dispatch. Foreground lifecycle remains
      // pending until this request's terminal event arrives on /api/ui/stream.
      return terminal;
    },
    [config.chat.defaultModel, getApiUrl],
  );

  const cancelStream = useCallback(async () => {
    const requestId = activeStreamRequestIdRef.current;
    if (!requestId) {
      return;
    }

    const pending = pendingStreamsRef.current.get(requestId);
    if (pending) {
      // Stop must win the acknowledgement race: abort the request before
      // sending cancellation, and ignore any late start/delta frames.
      pending.cancelRequested = true;
      setConnectionState('cancelling');
    }
    const acknowledgement = activeAcknowledgementRef.current;
    if (acknowledgement?.requestId === requestId) {
      acknowledgement.controller.abort();
      activeAcknowledgementRef.current = null;
    }

    const responsesUrl = getApiUrl('responses').replace(/\/$/, '');
    await requestAcknowledged(`${responsesUrl}/${encodeURIComponent(requestId)}/cancel`, {
      method: 'PUT',
      requestId: crypto.randomUUID(),
      errorLabel: 'cancelStream failed',
    }).catch((error) => {
      console.warn('Failed to cancel backend response stream', error);
    });
    // The matching `chat.response.cancelled` event owns terminal cleanup.
  }, [getApiUrl]);

  return { connectionState, sendMessageStream, cancelStream };
}
