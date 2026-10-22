import { useCallback, useEffect, useRef, useState } from 'react';
import { useConfig } from '../../../context/ConfigContext';
import { subscribeUiEvent } from '../../../realtime/realtimeEvents';
import { requestAcknowledged, withQuery } from '../../../store/http';
import { createUuid } from '../../../lib/uuid';

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

const FRONTEND_RESPONSE_OPTIONS_KEY = '__frontendResponse';
const RESPONSE_TERMINAL_DRAIN_MS = 50;
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
  if (pending.terminalTimer !== null) {
    clearTimeout(pending.terminalTimer);
    pending.terminalTimer = null;
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

function splitResponseMetadata(metadata) {
  if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) {
    return { backendMetadata: metadata, allowDesyncedCompletion: false };
  }

  const frontendOptions = metadata[FRONTEND_RESPONSE_OPTIONS_KEY];
  const allowDesyncedCompletion = Boolean(frontendOptions?.allowDesyncedCompletion);

  if (!Object.prototype.hasOwnProperty.call(metadata, FRONTEND_RESPONSE_OPTIONS_KEY)) {
    return { backendMetadata: metadata, allowDesyncedCompletion };
  }

  const { [FRONTEND_RESPONSE_OPTIONS_KEY]: _frontendOptions, ...backendMetadata } = metadata;
  return {
    backendMetadata: Object.keys(backendMetadata).length > 0 ? backendMetadata : null,
    allowDesyncedCompletion,
  };
}

function buildDesyncCompletionError(reason) {
  if (reason === 'cursor_expired') {
    return new Error('Response stream expired before completion');
  }
  if (reason === 'invalid_cursor') {
    return new Error('Response stream lost synchronization before completion');
  }
  return new Error('Response stream disconnected before completion');
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

  const finalizePendingTerminal = useCallback((requestId, pending) => {
    if (pending.finalized || pendingStreamsRef.current.get(requestId) !== pending) {
      return;
    }

    pending.finalized = true;
    const terminalEvent = pending.terminalEvent;
    const terminalType = terminalEvent?.type;
    const payload = terminalEvent?.payload || {};

    cleanupPendingTransport(pending);
    pendingStreamsRef.current.delete(requestId);
    if (activeAcknowledgementRef.current?.requestId === requestId) {
      activeAcknowledgementRef.current = null;
    }
    if (activeStreamRequestIdRef.current === requestId) {
      activeStreamRequestIdRef.current = null;
    }

    if (terminalType === 'chat.response.failed') {
      const message = typeof payload.error === 'string' && payload.error
        ? payload.error
        : 'Response generation failed';
      pending.onError?.(new Error(message));
      setConnectionState(deriveConnectionState(pendingStreamsRef.current, 'error'));
    } else if (terminalType === 'chat.response.completed' && pending.desynced && !pending.allowDesyncedCompletion) {
      pending.onError?.(buildDesyncCompletionError(pending.desyncReason));
      setConnectionState(deriveConnectionState(pendingStreamsRef.current, 'error'));
    } else {
      pending.onComplete?.(terminalType === 'chat.response.cancelled' ? 'cancelled' : '');
      setConnectionState(deriveConnectionState(pendingStreamsRef.current));
    }

    pending.resolve();
  }, []);

  const scheduleTerminalFinalization = useCallback((requestId, pending, delayMs = RESPONSE_TERMINAL_DRAIN_MS) => {
    if (!pending.terminalEvent || pending.finalized) {
      return;
    }
    if (pending.terminalTimer !== null) {
      clearTimeout(pending.terminalTimer);
    }
    pending.terminalTimer = setTimeout(() => {
      pending.terminalTimer = null;
      finalizePendingTerminal(requestId, pending);
    }, delayMs);
  }, [finalizePendingTerminal]);

  const openResponseStream = useCallback((requestId, pending, responsesUrl) => {
    const isCurrent = () => pendingStreamsRef.current.get(requestId) === pending;

    const failUnavailable = () => {
      cleanupPendingTransport(pending);
      if (!isCurrent()) {
        return;
      }
      pending.finalized = true;
      pendingStreamsRef.current.delete(requestId);
      if (activeAcknowledgementRef.current?.requestId === requestId) {
        activeAcknowledgementRef.current = null;
      }
      if (activeStreamRequestIdRef.current === requestId) {
        activeStreamRequestIdRef.current = null;
      }
      pending.onError?.(new Error('Response stream unavailable'));
      pending.resolve();
      setConnectionState(deriveConnectionState(pendingStreamsRef.current, 'error'));
    };

    const markDesynced = (reason = 'stream_unavailable') => {
      pending.desynced = true;
      pending.desyncReason = reason;
      cleanupPendingTransport(pending);
    };

    const scheduleReconnect = () => {
      if (!isCurrent() || pending.cancelRequested || pending.desynced || pending.terminalEvent) {
        return;
      }
      cleanupPendingTransport(pending);
      pending.connectAttempts += 1;
      if (pending.connectAttempts >= RESPONSE_CONNECT_FAILURE_LIMIT) {
        if (!pending.openedOnce) {
          failUnavailable();
        } else {
          markDesynced('stream_unavailable');
        }
        return;
      }
      pending.reconnectTimer = setTimeout(() => {
        pending.reconnectTimer = null;
        connect();
      }, RESPONSE_RECONNECT_DELAY_MS);
    };

    const connect = () => {
      if (!isCurrent() || pending.cancelRequested || pending.desynced || pending.terminalEvent) {
        return;
      }

      cleanupPendingTransport(pending);
      pending.lastEventAt = Date.now();

      const streamUrl = responseStreamUrl(responsesUrl, requestId, pending.lastEventId);
      const source = new EventSource(streamUrl);
      pending.eventSource = source;

      const handleContentFrame = (responseType, messageEvent) => {
        if (!isCurrent() || pending.eventSource !== source || pending.cancelRequested || pending.finalized) {
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
        pending.connectAttempts = 0;
        setConnectionState(deriveConnectionState(pendingStreamsRef.current));

        if (responseType === 'chat.response.delta') {
          const delta = typeof payload.delta === 'string' ? payload.delta : '';
          if (delta) {
            pending.onChunk?.(delta);
          }
        } else {
          pending.onStreamEvent?.(payload);
        }

        if (pending.terminalEvent) {
          scheduleTerminalFinalization(requestId, pending);
        }
      };

      source.onopen = () => {
        if (!isCurrent() || pending.eventSource !== source || pending.finalized) {
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
        if (!isCurrent() || pending.eventSource !== source || pending.finalized) {
          return;
        }
        pending.lastEventAt = Date.now();
      });
      source.addEventListener('chat.response.resync_required', (event) => {
        if (!isCurrent() || pending.eventSource !== source || pending.finalized) {
          return;
        }

        let reason = 'unknown';
        try {
          const payload = JSON.parse(event.data);
          if (typeof payload?.reason === 'string' && payload.reason) {
            reason = payload.reason;
          }
        } catch {
          // leave the fallback reason in place
        }

        markDesynced(reason);
        if (pending.terminalEvent) {
          scheduleTerminalFinalization(requestId, pending, 0);
        }
      });
      source.onerror = () => {
        if (!isCurrent() || pending.eventSource !== source || pending.finalized) {
          return;
        }

        if (pending.terminalEvent) {
          scheduleTerminalFinalization(requestId, pending);
          return;
        }
        if (pending.cancelRequested) {
          cleanupPendingTransport(pending);
          return;
        }

        scheduleReconnect();
      };
      pending.watchdogTimer = setInterval(() => {
        if (!isCurrent() || pending.eventSource !== source || pending.finalized || pending.terminalEvent) {
          return;
        }
        if (Date.now() - pending.lastEventAt > RESPONSE_HEARTBEAT_STALE_MS) {
          scheduleReconnect();
        }
      }, RESPONSE_WATCHDOG_POLL_MS);
    };

    connect();
  }, [scheduleTerminalFinalization]);

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

    pending.streamObserved = true;
    pending.terminalEvent = { type, payload };

    if (pending.desynced || pending.eventSource === null || pending.cancelRequested) {
      scheduleTerminalFinalization(requestId, pending, 0);
      return;
    }

    scheduleTerminalFinalization(requestId, pending);
  }, [scheduleTerminalFinalization]);

  useEffect(() => subscribeUiEvent(consumeStreamEvent), [consumeStreamEvent]);

  const sendMessageStream = useCallback(
    async (message, history = [], onChunk, onComplete, onError, onStreamEvent, metadata = null) => {
      const requestId = createUuid();
      const input = buildResponsesInput(history, message);
      const url = getApiUrl('responses');
      const { backendMetadata, allowDesyncedCompletion } = splitResponseMetadata(metadata);

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
        terminal,
        cancelRequested: false,
        acknowledged: false,
        streamObserved: false,
        eventSource: null,
        reconnectTimer: null,
        watchdogTimer: null,
        terminalTimer: null,
        lastEventAt: null,
        lastEventId: null,
        connectAttempts: 0,
        openedOnce: false,
        desynced: false,
        desyncReason: null,
        allowDesyncedCompletion,
        terminalEvent: null,
        finalized: false,
        receivedFrameIds: [],
      };

      // Register before dispatch: the backend can broadcast `started` before
      // the 202 acknowledgement reaches the browser.
      pendingStreamsRef.current.set(requestId, pending);
      activeStreamRequestIdRef.current = requestId;
      activeAcknowledgementRef.current = { requestId, controller: acknowledgementController };
      setConnectionState(deriveConnectionState(pendingStreamsRef.current));

      const body = { model: config.chat.defaultModel, input, stream: true };
      if (backendMetadata && typeof backendMetadata === 'object') {
        body.metadata = backendMetadata;
      }

      try {
        await requestAcknowledged(url, {
          method: 'POST',
          body,
          requestId,
          signal: acknowledgementController.signal,
        });
        if (activeAcknowledgementRef.current?.requestId === requestId) {
          activeAcknowledgementRef.current = null;
        }
        if (pendingStreamsRef.current.get(requestId) === pending && !pending.cancelRequested && !pending.terminalEvent) {
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
          pending.finalized = true;
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

  // Attach to a response stream this hook instance did not dispatch itself —
  // e.g. a generation another surface (quick chat, the Chat page, another
  // browser tab) started into a thread this component is currently viewing.
  // Unlike sendMessageStream's pending-stream machinery, this never POSTs and
  // never owns cancel/acknowledgement lifecycle; the caller is expected to
  // decide when the generation is over (via the thread's Redux
  // generatingChatIds) and call the returned cleanup function itself. No
  // `after` cursor is sent, so the backend replays its full retained frame
  // buffer from the start — this is what makes "join in progress" work.
  const observeResponseStream = useCallback((requestId, { onChunk, onStreamEvent } = {}) => {
    const responsesUrl = getApiUrl('responses');
    let source = null;
    let reconnectTimer = null;
    let connectAttempts = 0;
    let stopped = false;
    const receivedFrameIds = [];

    const rememberFrameId = (frameId) => {
      if (!frameId) return;
      receivedFrameIds.push(frameId);
      if (receivedFrameIds.length > MAX_RECEIVED_RESPONSE_FRAME_IDS) {
        receivedFrameIds.shift();
      }
    };
    const hasSeenFrameId = (frameId) => Boolean(frameId) && receivedFrameIds.includes(frameId);

    const cleanup = () => {
      source?.close();
      source = null;
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
    };

    const connect = () => {
      if (stopped) return;
      cleanup();

      const streamUrl = responseStreamUrl(responsesUrl, requestId);
      const nextSource = new EventSource(streamUrl);
      source = nextSource;

      const handleContentFrame = (responseType, messageEvent) => {
        if (source !== nextSource) return;
        let payload;
        try {
          payload = JSON.parse(messageEvent.data);
        } catch {
          return;
        }
        const frameId = messageEvent.lastEventId;
        if (hasSeenFrameId(frameId)) return;
        rememberFrameId(frameId);
        connectAttempts = 0;

        if (responseType === 'chat.response.delta') {
          const delta = typeof payload.delta === 'string' ? payload.delta : '';
          if (delta) {
            onChunk?.(delta);
          }
        } else {
          onStreamEvent?.(payload);
        }
      };

      nextSource.addEventListener('chat.response.delta', (event) => {
        handleContentFrame('chat.response.delta', event);
      });
      nextSource.addEventListener('chat.response.event', (event) => {
        handleContentFrame('chat.response.event', event);
      });
      nextSource.onerror = () => {
        if (source !== nextSource || stopped) return;
        cleanup();
        connectAttempts += 1;
        if (connectAttempts >= RESPONSE_CONNECT_FAILURE_LIMIT) {
          return;
        }
        reconnectTimer = setTimeout(() => {
          reconnectTimer = null;
          connect();
        }, RESPONSE_RECONNECT_DELAY_MS);
      };
    };

    connect();
    activeStreamRequestIdRef.current = requestId;

    return () => {
      stopped = true;
      cleanup();
      if (activeStreamRequestIdRef.current === requestId) {
        activeStreamRequestIdRef.current = null;
      }
    };
  }, [getApiUrl]);

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
      requestId: createUuid(),
    }).catch((error) => {
      console.warn('Failed to cancel backend response stream', error);
    });

    await pending?.terminal;
  }, [getApiUrl]);

  return { connectionState, sendMessageStream, cancelStream, observeResponseStream };
}
