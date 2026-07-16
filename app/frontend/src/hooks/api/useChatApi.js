import { useCallback, useEffect, useRef, useState } from 'react';
import { useConfig } from '../../context/ConfigContext';
import { subscribeUiEvent } from '../../store/realtimeEvents';
import { requestAcknowledged, withQuery } from '../../store/http';
import {
  chatPerformanceNow,
  isChatPerformanceTracingEnabled,
  traceChatPerformance,
} from '../../components/chat/chatPerformanceTrace';

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

function streamEventTraceDetails(payload) {
  const metadata = payload?.metadata && typeof payload.metadata === 'object' ? payload.metadata : {};
  return {
    eventType: typeof payload?.event_type === 'string' ? payload.event_type : 'unknown',
    contentLength: typeof payload?.content === 'string' ? payload.content.length : 0,
    toolName: typeof metadata.tool_name === 'string' ? metadata.tool_name : '',
    toolCallId: typeof metadata.tool_call_id === 'string' ? metadata.tool_call_id : '',
    subagentName: typeof metadata.subagent_name === 'string' ? metadata.subagent_name : '',
    nestedToolName: typeof metadata.nested_tool_name === 'string' ? metadata.nested_tool_name : '',
    durationMs: Number.isFinite(metadata.duration_ms) ? metadata.duration_ms : null,
  };
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

    const scheduleReconnect = (traceName) => {
      if (!isCurrent() || pending.cancelRequested || pending.desynced) {
        return;
      }
      cleanupPendingTransport(pending);
      pending.connectAttempts += 1;
      traceChatPerformance(traceName, {
        requestId,
        connectAttempts: pending.connectAttempts,
      });
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
      const tracing = isChatPerformanceTracingEnabled();
      pending.lastEventAt = Date.now();
      const streamUrl = responseStreamUrl(responsesUrl, requestId, pending.lastEventId);
      if (tracing) {
        traceChatPerformance('chat_sse.connect_started', {
          requestId,
          after: pending.lastEventId,
          connectAttempts: pending.connectAttempts,
        });
      }
      const source = new EventSource(streamUrl);
      pending.eventSource = source;

      const handleContentFrame = (responseType, messageEvent) => {
        if (!isCurrent() || pending.eventSource !== source || pending.cancelRequested) {
          return;
        }
        const callbackStartedAt = tracing ? chatPerformanceNow() : 0;
        let payload;
        try {
          payload = JSON.parse(messageEvent.data);
        } catch {
          return;
        }
        const parsedAt = tracing ? chatPerformanceNow() : 0;
        const frameId = messageEvent.lastEventId;
        if (hasSeenResponseFrameId(pending, frameId)) {
          return;
        }
        rememberResponseFrameId(pending, frameId);
        const previousArrivalAt = pending.lastEventAt;
        pending.lastEventId = frameId || pending.lastEventId;
        pending.lastEventAt = Date.now();
        pending.openedOnce = true;
        pending.streamObserved = true;
        setConnectionState(deriveConnectionState(pendingStreamsRef.current));

        const arrivalGapMs = previousArrivalAt === null ? null : pending.lastEventAt - previousArrivalAt;
        const delta = responseType === 'chat.response.delta' && typeof payload.delta === 'string'
          ? payload.delta
          : '';
        const eventType = responseType === 'chat.response.event'
          ? (typeof payload.event_type === 'string' ? payload.event_type : 'unknown')
          : '';
        if (tracing) {
          traceChatPerformance('chat_sse.frame_received', {
            requestId,
            responseType,
            deltaLength: delta.length,
            eventType,
            arrivalGapMs,
            parseMs: parsedAt - callbackStartedAt,
            runtimeStreamSeq: Number.isFinite(payload.runtime_stream_seq)
              ? payload.runtime_stream_seq
              : null,
            runtimeToBrowserMs: Number.isFinite(payload.runtime_emitted_at_ms)
              ? Date.now() - payload.runtime_emitted_at_ms
              : null,
          }, {
            consoleOutput: responseType !== 'chat.response.delta'
              || (arrivalGapMs !== null && arrivalGapMs >= 50),
          });
        }

        const userCallbackStartedAt = tracing ? chatPerformanceNow() : 0;
        if (responseType === 'chat.response.delta') {
          if (delta) {
            pending.onChunk?.(delta);
          }
          if (tracing) {
            traceChatPerformance('chat_api.delta_handler', {
              requestId,
              deltaLength: delta.length,
              callbackDurationMs: chatPerformanceNow() - userCallbackStartedAt,
              handlerDurationMs: chatPerformanceNow() - callbackStartedAt,
            }, { consoleOutput: false });
          }
          return;
        }

        pending.onStreamEvent?.(payload);
        if (tracing) {
          traceChatPerformance('chat_api.stream_event_handler', {
            requestId,
            ...streamEventTraceDetails(payload),
            callbackDurationMs: chatPerformanceNow() - userCallbackStartedAt,
            handlerDurationMs: chatPerformanceNow() - callbackStartedAt,
          });
        }
      };

      source.onopen = () => {
        if (!isCurrent() || pending.eventSource !== source) {
          return;
        }
        pending.lastEventAt = Date.now();
        if (tracing) {
          traceChatPerformance('chat_sse.connected', { requestId });
        }
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
        if (tracing) {
          traceChatPerformance('chat_sse.heartbeat', { requestId }, { consoleOutput: false });
        }
      });
      source.addEventListener('chat.response.resync_required', (event) => {
        if (!isCurrent() || pending.eventSource !== source) {
          return;
        }
        let payload = {};
        try {
          payload = JSON.parse(event.data);
        } catch {
          // A malformed resync signal still means this transport is unusable.
        }
        pending.desynced = true;
        cleanupPendingTransport(pending);
        if (tracing) {
          traceChatPerformance('chat_sse.resync_required', {
            requestId,
            reason: payload.reason || 'unknown',
          });
        }
      });
      source.onerror = () => {
        if (!isCurrent() || pending.eventSource !== source) {
          return;
        }
        if (pending.cancelRequested) {
          cleanupPendingTransport(pending);
          return;
        }
        scheduleReconnect('chat_sse.onerror_reconnect');
      };
      pending.watchdogTimer = setInterval(() => {
        if (!isCurrent() || pending.eventSource !== source) {
          return;
        }
        if (Date.now() - pending.lastEventAt > RESPONSE_HEARTBEAT_STALE_MS) {
          scheduleReconnect('chat_sse.stale_reconnect');
        }
      }, RESPONSE_WATCHDOG_POLL_MS);
    };

    connect();
  }, []);

  const consumeStreamEvent = useCallback((event) => {
    const tracing = isChatPerformanceTracingEnabled();
    const handlerStartedAt = tracing ? chatPerformanceNow() : 0;
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
      if (tracing) {
        traceChatPerformance('chat_api.unmatched_frame', {
          requestId: payload.request_id || '',
          responseType: type,
          handlerDurationMs: chatPerformanceNow() - handlerStartedAt,
        });
      }
      return;
    }

    if (type === 'chat.response.started') {
      pending.streamObserved = true;
      if (!pending.cancelRequested) {
        setConnectionState(deriveConnectionState(pendingStreamsRef.current));
      }
      if (tracing) {
        traceChatPerformance('chat_api.response_started', {
          requestId,
          dispatchToStartedMs: pending.dispatchedAt === null ? null : chatPerformanceNow() - pending.dispatchedAt,
          handlerDurationMs: chatPerformanceNow() - handlerStartedAt,
        });
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
    if (tracing) {
      traceChatPerformance('chat_api.terminal', {
        requestId,
        responseType: type,
        handlerDurationMs: chatPerformanceNow() - handlerStartedAt,
      });
    }
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
        dispatchedAt: isChatPerformanceTracingEnabled() ? chatPerformanceNow() : null,
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
      if (isChatPerformanceTracingEnabled()) {
        traceChatPerformance('chat_api.dispatch_started', {
          requestId,
          inputMessageCount: input.length,
          inputCharacterCount: input.reduce((total, entry) => total + entry.content.length, 0),
        });
      }
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
        if (isChatPerformanceTracingEnabled()) {
          traceChatPerformance('chat_api.dispatch_acknowledged', {
            requestId,
            acknowledgementMs: pending.dispatchedAt === null ? null : chatPerformanceNow() - pending.dispatchedAt,
          });
        }
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
        if (isChatPerformanceTracingEnabled()) {
          traceChatPerformance('chat_api.dispatch_failed', {
            requestId,
            observedStream: pending.streamObserved,
            failureMs: pending.dispatchedAt === null ? null : chatPerformanceNow() - pending.dispatchedAt,
          });
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
