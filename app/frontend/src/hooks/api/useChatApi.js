import { useCallback, useEffect, useRef, useState } from 'react';
import { useConfig } from '../../context/ConfigContext';
import { subscribeUiEvent } from '../../store/realtimeEvents';
import { requestAcknowledged, withQuery } from '../../store/http';

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

function deriveConnectionState(pendingStreams, emptyState = 'disconnected') {
  const pending = [...pendingStreams.values()];
  if (pending.length === 0) {
    return emptyState;
  }
  if (pending.some((stream) => stream.cancelRequested)) {
    return 'cancelling';
  }
  if (pending.some((stream) => stream.acknowledged || stream.streamObserved)) {
    return 'connected';
  }
  return 'connecting';
}


const RESPONSE_HEARTBEAT_STALE_MS = 45000;
const RESPONSE_WATCHDOG_POLL_MS = 15000;
const RESPONSE_RECONNECT_DELAY_MS = 1000;
const RESPONSE_CONNECT_FAILURE_LIMIT = 5;
const MAX_RECEIVED_RESPONSE_FRAME_IDS = 1024;

function responseStreamUrl(responsesUrl, requestId, after) {
  const url = `${responsesUrl.replace(/\/$/, '')}/${encodeURIComponent(requestId)}/stream`;
  return after ? withQuery(url, { after }) : url;
}

function cleanupPendingTransport(pending) {
  pending.eventSource?.close();
  pending.eventSource = null;
  if (pending.reconnectTimer !== null) {
    clearTimeout(pending.reconnectTimer);
    pending.reconnectTimer = null;
  }
  if (pending.watchdogTimer !== null) {
    clearInterval(pending.watchdogTimer);
    pending.watchdogTimer = null;
  }
}

function rememberResponseFrameId(pending, frameId) {
  if (!frameId) {
    return;
  }
  pending.receivedFrameIds.push(frameId);
  if (pending.receivedFrameIds.length > MAX_RECEIVED_RESPONSE_FRAME_IDS) {
    pending.receivedFrameIds.shift();
  }
}

function hasSeenResponseFrameId(pending, frameId) {
  return Boolean(frameId) && pending.receivedFrameIds.includes(frameId);
}

/**
 * Foreground Responses API lifecycle shared by the conversation page and
 * runtime shell. Canonical ui.event frames own lifecycle; each active response
 * receives content over its request-scoped EventSource.
 */
export function useChatApi() {
  const { config, getApiUrl } = useConfig();
  const [connectionState, setConnectionState] = useState('disconnected');
  const activeStreamRequestIdRef = useRef(null);
  const activeAcknowledgementRef = useRef(null);
  const pendingStreamsRef = useRef(new Map());

  const openResponseStream = useCallback((requestId, pending, responsesUrl) => {
    const isCurrent = () => pendingStreamsRef.current.get(requestId) === pending;

    const failUnavailable = () => {
      cleanupPendingTransport(pending);
      if (!isCurrent()) {
        return;
      }
      pendingStreamsRef.current.delete(requestId);
      if (activeStreamRequestIdRef.current === requestId) {
        activeStreamRequestIdRef.current = null;
      }
      pending.onError?.(new Error('Response stream unavailable'));
      pending.resolve();
      setConnectionState(deriveConnectionState(pendingStreamsRef.current, 'error'));
    };

    const scheduleReconnect = () => {
      if (!isCurrent() || pending.cancelRequested || pending.desynced) {
        return;
      }
      cleanupPendingTransport(pending);
      pending.connectAttempts += 1;
      if (pending.connectAttempts >= RESPONSE_CONNECT_FAILURE_LIMIT) {
        if (!pending.openedOnce) {
          failUnavailable();
        } else {
          pending.desynced = true;
          cleanupPendingTransport(pending);
        }
        return;
      }
      pending.reconnectTimer = setTimeout(() => {
        pending.reconnectTimer = null;
        connect();
      }, RESPONSE_RECONNECT_DELAY_MS);
    };

    const connect = () => {
      if (!isCurrent() || pending.cancelRequested || pending.desynced) {
        return;
      }
      cleanupPendingTransport(pending);
      pending.lastEventAt = Date.now();
      const streamUrl = responseStreamUrl(responsesUrl, requestId, pending.lastEventId);
      const source = new EventSource(streamUrl);
      pending.eventSource = source;

      const handleContentFrame = (responseType, messageEvent) => {
        if (!isCurrent() || pending.eventSource !== source || pending.cancelRequested) {
          return;
        }
        let payload;
        try {
          payload = JSON.parse(messageEvent.data);
        } catch {
          return;
        }
        const frameId = messageEvent.lastEventId;
        if (hasSeenResponseFrameId(pending, frameId)) {
          return;
        }
        rememberResponseFrameId(pending, frameId);
        pending.lastEventId = frameId || pending.lastEventId;
        pending.lastEventAt = Date.now();
        pending.openedOnce = true;
        pending.streamObserved = true;
        setConnectionState(deriveConnectionState(pendingStreamsRef.current));

        const delta = responseType === 'chat.response.delta' && typeof payload.delta === 'string'
          ? payload.delta
          : '';

        if (responseType === 'chat.response.delta') {
          if (delta) {
            pending.onChunk?.(delta);
          }
          return;
        }

        pending.onStreamEvent?.(payload);
      };

      source.onopen = () => {
        if (!isCurrent() || pending.eventSource !== source) {
          return;
        }
        pending.lastEventAt = Date.now();
      };
      source.addEventListener('chat.response.delta', (event) => {
        handleContentFrame('chat.response.delta', event);
      });
      source.addEventListener('chat.response.event', (event) => {
        handleContentFrame('chat.response.event', event);
      });
      source.addEventListener('chat.response.heartbeat', () => {
        if (!isCurrent() || pending.eventSource !== source) {
          return;
        }
        pending.lastEventAt = Date.now();
      });
      source.addEventListener('chat.response.resync_required', () => {
        if (!isCurrent() || pending.eventSource !== source) {
          return;
        }
        pending.desynced = true;
        cleanupPendingTransport(pending);
      });
      source.onerror = () => {
        if (!isCurrent() || pending.eventSource !== source) {
          return;
        }
        if (pending.cancelRequested) {
          cleanupPendingTransport(pending);
          return;
        }
        scheduleReconnect();
      };
      pending.watchdogTimer = setInterval(() => {
        if (!isCurrent() || pending.eventSource !== source) {
          return;
        }
        if (Date.now() - pending.lastEventAt > RESPONSE_HEARTBEAT_STALE_MS) {
          scheduleReconnect();
        }
      }, RESPONSE_WATCHDOG_POLL_MS);
    };

    connect();
  }, []);

  const consumeStreamEvent = useCallback((event) => {
    const type = event?.type;
    const payload = event?.payload || event?.data;
    if (type === 'chat.response.cancel_requested') {
      return;
    }
    if (!['chat.response.started', 'chat.response.completed', 'chat.response.cancelled', 'chat.response.failed'].includes(type) || !payload) {
      return;
    }

    const requestId = payload.request_id;
    const pending = requestId ? pendingStreamsRef.current.get(requestId) : null;
    if (!pending) {
      return;
    }

    if (type === 'chat.response.started') {
      pending.streamObserved = true;
      if (!pending.cancelRequested) {
        setConnectionState(deriveConnectionState(pendingStreamsRef.current));
      }
      return;
    }

    cleanupPendingTransport(pending);
    pending.streamObserved = true;
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
      setConnectionState(deriveConnectionState(pendingStreamsRef.current, 'error'));
    } else {
      pending.onComplete?.(type === 'chat.response.cancelled' ? 'cancelled' : '');
      setConnectionState(deriveConnectionState(pendingStreamsRef.current));
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
        acknowledged: false,
        streamObserved: false,
        eventSource: null,
        reconnectTimer: null,
        watchdogTimer: null,
        lastEventAt: null,
        lastEventId: null,
        connectAttempts: 0,
        openedOnce: false,
        desynced: false,
        receivedFrameIds: [],
      };

      // Register before dispatch: the backend can broadcast `started` before
      // the 202 acknowledgement reaches the browser.
      pendingStreamsRef.current.set(requestId, pending);
      activeStreamRequestIdRef.current = requestId;
      activeAcknowledgementRef.current = { requestId, controller: acknowledgementController };
      setConnectionState(deriveConnectionState(pendingStreamsRef.current));

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
          pending.acknowledged = true;
          setConnectionState(deriveConnectionState(pendingStreamsRef.current));
          openResponseStream(requestId, pending, url);
        }
      } catch (error) {
        if (activeAcknowledgementRef.current?.requestId === requestId) {
          activeAcknowledgementRef.current = null;
        }
        const currentPending = pendingStreamsRef.current.get(requestId);
        if (currentPending === pending && !pending.cancelRequested && !pending.streamObserved) {
          cleanupPendingTransport(pending);
          pendingStreamsRef.current.delete(requestId);
          if (activeStreamRequestIdRef.current === requestId) {
            activeStreamRequestIdRef.current = null;
          }
          setConnectionState(deriveConnectionState(pendingStreamsRef.current, 'error'));
          onError?.(error);
          resolveTerminal();
        } else if (currentPending === pending) {
          setConnectionState(deriveConnectionState(pendingStreamsRef.current));
        }
      }

      return terminal;
    },
    [config.chat.defaultModel, getApiUrl, openResponseStream],
  );

  const cancelStream = useCallback(async () => {
    const requestId = activeStreamRequestIdRef.current;
    if (!requestId) {
      return;
    }

    const pending = pendingStreamsRef.current.get(requestId);
    if (pending) {
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
    // chat.response.cancel_requested is non-terminal. The target request's
    // canonical chat.response.cancelled event owns cleanup.
  }, [getApiUrl]);

  return { connectionState, sendMessageStream, cancelStream };
}
