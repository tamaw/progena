import { useCallback, useEffect, useRef, useState } from 'react';
import { useConfig } from '../../../context/ConfigContext';
import { subscribeUiEvent } from '../../../realtime/realtimeEvents';
import { requestAcknowledged, withQuery } from '../../../store/http';
import { createUuid } from '../../../lib/uuid';
import { CHAT_CONTENT_EVENT, UI_EVENT } from '../../../realtime/eventVocabulary';


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
// The canonical terminal travels on the UI stream and can overtake content
// still in flight on the request stream. Its close is the drain signal; this
// bounds the wait when that close never arrives.
const RESPONSE_TERMINAL_CONTENT_TIMEOUT_MS = 5000;
const RESPONSE_HEARTBEAT_STALE_MS = 45000;
const RESPONSE_WATCHDOG_POLL_MS = 15000;
const RESPONSE_RECONNECT_DELAY_MS = 1000;
const RESPONSE_CONNECT_FAILURE_LIMIT = 5;
const MAX_RECEIVED_RESPONSE_FRAME_IDS = 1024;

function responseStreamUrl(responsesUrl, requestId, after) {
  const url = `${responsesUrl.replace(/\/$/, '')}/${encodeURIComponent(requestId)}/stream`;
  return after ? withQuery(url, { after }) : url;
}

function closePendingSource(pending) {
  pending.eventSource?.close();
  pending.eventSource = null;
  if (pending.watchdogTimer !== null) {
    clearInterval(pending.watchdogTimer);
    pending.watchdogTimer = null;
  }
}

function cleanupPendingTransport(pending) {
  closePendingSource(pending);
  if (pending.reconnectTimer !== null) {
    clearTimeout(pending.reconnectTimer);
    pending.reconnectTimer = null;
  }
  if (pending.terminalTimer !== null) {
    clearTimeout(pending.terminalTimer);
    pending.terminalTimer = null;
  }
}

// One request's transport state, shared by sends and observed responses.
function createPendingStream({
  onChunk, onComplete, onError, onStreamEvent,
  acknowledged = false, allowDesyncedCompletion = false, owner = null,
}) {
  let resolve;
  const terminal = new Promise((settle) => { resolve = settle; });
  return {
    onChunk, onComplete, onError, onStreamEvent, resolve, terminal,
    allowDesyncedCompletion, owner, acknowledged,
    cancelRequested: false, streamObserved: false, eventSource: null,
    reconnectTimer: null, watchdogTimer: null, terminalTimer: null, lastEventAt: null,
    lastEventId: null, connectAttempts: 0, openedOnce: false, desynced: false,
    desyncReason: null, terminalEvent: null, finalized: false, receivedFrameIds: [],
    // Set when the request stream closed after the terminal: all content arrived.
    contentDrained: false, connect: null,
  };
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
  if (!Object.hasOwn(metadata, FRONTEND_RESPONSE_OPTIONS_KEY)) {
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

    if (terminalType === UI_EVENT.CHAT_RESPONSE_FAILED) {
      const message = typeof payload.error === 'string' && payload.error
        ? payload.error
        : 'Response generation failed';
      pending.onError?.(new Error(message));
      setConnectionState(deriveConnectionState(pendingStreamsRef.current, 'error'));
    } else if (
      terminalType === UI_EVENT.CHAT_RESPONSE_COMPLETED
      && pending.desynced
      && !pending.allowDesyncedCompletion
    ) {
      pending.onError?.(buildDesyncCompletionError(pending.desyncReason));
      setConnectionState(deriveConnectionState(pendingStreamsRef.current, 'error'));
    } else {
      pending.onComplete?.(
        terminalType === UI_EVENT.CHAT_RESPONSE_CANCELLED ? 'cancelled' : 'completed',
        { contentComplete: pending.contentDrained },
      );
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
      if (!isCurrent() || pending.cancelRequested || pending.desynced || pending.finalized) {
        return;
      }

      closePendingSource(pending);
      pending.lastEventAt = Date.now();

      const streamUrl = responseStreamUrl(responsesUrl, requestId, pending.lastEventId);
      const source = new EventSource(streamUrl);
      let sourceOpened = false;
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
        sourceOpened = true;
        pending.lastEventId = frameId || pending.lastEventId;
        pending.lastEventAt = Date.now();
        pending.openedOnce = true;
        pending.streamObserved = true;
        pending.connectAttempts = 0;
        setConnectionState(deriveConnectionState(pendingStreamsRef.current));

        if (responseType === CHAT_CONTENT_EVENT.DELTA) {
          const delta = typeof payload.delta === 'string' ? payload.delta : '';
          if (delta) {
            pending.onChunk?.(delta);
          }
        } else {
          pending.onStreamEvent?.(payload);
        }
      };

      source.onopen = () => {
        if (!isCurrent() || pending.eventSource !== source || pending.finalized) {
          return;
        }
        sourceOpened = true;
        pending.lastEventAt = Date.now();
      };
      source.addEventListener(CHAT_CONTENT_EVENT.DELTA, (event) => {
        handleContentFrame(CHAT_CONTENT_EVENT.DELTA, event);
      });
      source.addEventListener(CHAT_CONTENT_EVENT.EVENT, (event) => {
        handleContentFrame(CHAT_CONTENT_EVENT.EVENT, event);
      });
      source.addEventListener(CHAT_CONTENT_EVENT.HEARTBEAT, () => {
        if (!isCurrent() || pending.eventSource !== source || pending.finalized) {
          return;
        }
        pending.lastEventAt = Date.now();
      });
      source.addEventListener(CHAT_CONTENT_EVENT.RESYNC_REQUIRED, (event) => {
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
          // After the terminal, the server closes an opened stream only once
          // every retained frame is flushed. A failed connection proves nothing.
          pending.contentDrained = sourceOpened;
          closePendingSource(pending);
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

    pending.connect = connect;
    connect();
  }, [scheduleTerminalFinalization]);

  // Finalize a terminal response only after its request stream has drained,
  // reopening it from the last cursor when it is between connections.
  const awaitTerminalContent = useCallback((requestId, pending) => {
    if (pending.eventSource === null) {
      if (pending.reconnectTimer !== null) {
        clearTimeout(pending.reconnectTimer);
        pending.reconnectTimer = null;
      }
      if (pending.acknowledged) pending.connect?.();
    }
    scheduleTerminalFinalization(requestId, pending, RESPONSE_TERMINAL_CONTENT_TIMEOUT_MS);
  }, [scheduleTerminalFinalization]);

  const consumeStreamEvent = useCallback((event) => {
    const type = event?.type;
    const payload = event?.payload || event?.data;
    if (type === UI_EVENT.CHAT_RESPONSE_CANCEL_REQUESTED) {
      return;
    }
    if (![UI_EVENT.CHAT_RESPONSE_STARTED, UI_EVENT.CHAT_RESPONSE_COMPLETED, UI_EVENT.CHAT_RESPONSE_CANCELLED, UI_EVENT.CHAT_RESPONSE_FAILED].includes(type) || !payload) {
      return;
    }

    const requestId = payload.request_id;
    const pending = requestId ? pendingStreamsRef.current.get(requestId) : null;
    if (!pending) {
      return;
    }

    if (type === UI_EVENT.CHAT_RESPONSE_STARTED) {
      pending.streamObserved = true;
      if (!pending.cancelRequested) {
        setConnectionState(deriveConnectionState(pendingStreamsRef.current));
      }
      return;
    }

    pending.streamObserved = true;
    pending.terminalEvent = { type, payload };

    if (pending.desynced || pending.cancelRequested) {
      scheduleTerminalFinalization(requestId, pending, 0);
      return;
    }

    awaitTerminalContent(requestId, pending);
  }, [awaitTerminalContent, scheduleTerminalFinalization]);

  useEffect(() => subscribeUiEvent(consumeStreamEvent), [consumeStreamEvent]);

  const sendMessageStream = useCallback(
    async (message, history = [], onChunk, onComplete, onError, onStreamEvent, metadata = null, owner = null) => {
      const requestId = createUuid();
      const input = buildResponsesInput(history, message);
      const url = getApiUrl('responses');
      const { backendMetadata, allowDesyncedCompletion } = splitResponseMetadata(metadata);

      const acknowledgementController = new AbortController();
      const pending = createPendingStream({
        onChunk, onComplete, onError, onStreamEvent, allowDesyncedCompletion, owner,
      });
      const { resolve: resolveTerminal, terminal } = pending;

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
        if (pendingStreamsRef.current.get(requestId) === pending && !pending.cancelRequested && !pending.finalized) {
          pending.acknowledged = true;
          setConnectionState(deriveConnectionState(pendingStreamsRef.current));
          // A terminal that overtook the acknowledgement still has retained
          // content to replay; its close then completes the request.
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

  const queueMessage = useCallback(async (message, history = [], metadata = null, requestId = createUuid()) => {
    const body = { model: config.chat.defaultModel, input: buildResponsesInput(history, message), stream: true };
    if (metadata && typeof metadata === 'object') body.metadata = metadata;
    return requestAcknowledged(getApiUrl('responses'), { method: 'POST', body, requestId });
  }, [config.chat.defaultModel, getApiUrl]);

  // Follow an already-dispatched response using the same pending transport as
  // a foreground send. This registers canonical terminal events before opening
  // SSE, preserving EOF/cursor draining without issuing another POST.
  const observeResponseStream = useCallback((requestId, {
    onChunk,
    onComplete,
    onError,
    onStreamEvent,
    onAdopt,
  } = {}) => {
    const existing = pendingStreamsRef.current.get(requestId);
    if (existing) {
      existing.onChunk = onChunk;
      existing.onComplete = onComplete;
      existing.onError = onError;
      existing.onStreamEvent = onStreamEvent;
      onAdopt?.(existing.owner);
      return () => {
        if (pendingStreamsRef.current.get(requestId) !== existing) return;
        cleanupPendingTransport(existing);
        existing.finalized = true;
        pendingStreamsRef.current.delete(requestId);
        if (activeStreamRequestIdRef.current === requestId) activeStreamRequestIdRef.current = null;
        setConnectionState(deriveConnectionState(pendingStreamsRef.current));
        existing.resolve();
      };
    }

    const pending = createPendingStream({
      onChunk, onComplete, onError, onStreamEvent, acknowledged: true,
    });
    pendingStreamsRef.current.set(requestId, pending);
    activeStreamRequestIdRef.current = requestId;
    setConnectionState(deriveConnectionState(pendingStreamsRef.current));
    openResponseStream(requestId, pending, getApiUrl('responses'));
    return () => {
      if (pendingStreamsRef.current.get(requestId) !== pending) return;
      cleanupPendingTransport(pending);
      pending.finalized = true;
      pendingStreamsRef.current.delete(requestId);
      if (activeStreamRequestIdRef.current === requestId) activeStreamRequestIdRef.current = null;
      setConnectionState(deriveConnectionState(pendingStreamsRef.current));
      pending.resolve();
    };
  }, [getApiUrl, openResponseStream]);

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

  return { connectionState, sendMessageStream, queueMessage, cancelStream, observeResponseStream };
}
