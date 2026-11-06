import { createAction, createListenerMiddleware, isFulfilled, isPending, isRejected } from '@reduxjs/toolkit';
import {
  realtimeConnected,
  realtimeConnecting,
  realtimeDisconnected,
  realtimeErrored,
  realtimeStreamReady,
  uiEventReceived,
  registerPendingOp,
  failPendingOp,
  resolvePendingOp,
} from './realtimeSlice';
import { applyUiEvent } from '../store/uiEvents';
import { publishUiEvent } from './realtimeEvents';
import { realtimeClientId, setRealtimeClientReady } from './realtimeClient';
import { notifyStreamReady } from './streamLifecycle';
import { registerHeldTopics } from './streamTopics';
import {
  cancelRecovery,
  consumeStaleAcknowledgement,
  forgetRequest,
  holdPendingForRecovery,
  noteAcknowledged,
  noteRequestStarted,
  recoverOnReady,
} from './requestRecovery';
import { withQuery } from '../store/http';
import { UI_EVENT, UI_SSE_EVENT } from './eventVocabulary';

export const startRealtimeStream = createAction('realtime/startStream');
export const stopRealtimeStream = createAction('realtime/stopStream');

export const realtimeListenerMiddleware = createListenerMiddleware();

let eventSource = null;
let activeStreamUrl = null;

// The backend emits a `connection.heartbeat` ui.event every 15s
// (SSE_HEARTBEAT_INTERVAL). A stream silent for longer than this is deaf:
// the server evicted the subscriber or the socket is half-open — states
// EventSource never surfaces through onerror, so it never reconnects.
const HEARTBEAT_STALE_MS = 45000;
const WATCHDOG_POLL_MS = 15000;


function requestIdFor(action) {
  return action.meta?.requestId || action.meta?.arg?.requestId || null;
}

function invalidEnvelopeReason(event) {
  if (!event || typeof event !== 'object' || Array.isArray(event)) {
    return 'envelope must be an object';
  }
  if (typeof event.id !== 'string' || !event.id) {
    return 'envelope id is required';
  }
  if (typeof event.type !== 'string' || !event.type) {
    return 'envelope type is required';
  }
  if (!event.payload || typeof event.payload !== 'object' || Array.isArray(event.payload)) {
    return 'envelope payload must be an object';
  }
  return null;
}

function acknowledgementFor(action) {
  const payload = action.payload;
  const acknowledgement = payload?.ack || payload;
  if (
    acknowledgement?.request_id
    && Array.isArray(acknowledgement.event_types)
    && acknowledgement.event_types.length > 0
    && acknowledgement.event_types.every((eventType) => typeof eventType === 'string' && eventType)
  ) {
    return acknowledgement;
  }
  return null;
}

function failPendingOperations(listenerApi, error) {
  const pendingOps = listenerApi.getState().realtime.pendingOps || {};
  for (const requestId of Object.keys(pendingOps)) {
    listenerApi.dispatch(failPendingOp({ requestId, error }));
  }
}

// A stream that ends because the server is shutting down, or that the browser
// gave up on, is reopened after a backoff that doubles from one second to a cap,
// so a restarting server is neither hammered nor reconnected to while draining.
// The count restarts once a connection has stayed up long enough to be stable.
const STABLE_STREAM_MS = 10000;
const MAX_RECONNECT_DELAY_MS = 8000;
const EVENT_SOURCE_CLOSED = 2;
let reconnectTimer = null;
let reconnectAttempts = 0;
let connectedAt = 0;

export function reconnectDelayMs(attempts, random = Math.random) {
  const base = Math.min(1000 * 2 ** Math.max(0, attempts), MAX_RECONNECT_DELAY_MS);
  return Math.round(base * (0.75 + random() * 0.5));
}

function reconnectAfterBackoff(listenerApi, source, watchdog, streamUrl, error) {
  clearInterval(watchdog);
  source.close();
  eventSource = null;
  activeStreamUrl = null;
  setRealtimeClientReady(false);
  const wasStable = connectedAt > 0 && Date.now() - connectedAt >= STABLE_STREAM_MS;
  connectedAt = 0;
  reconnectAttempts = wasStable ? 0 : reconnectAttempts + 1;
  listenerApi.dispatch(realtimeErrored(error));
  holdPendingForRecovery(listenerApi);
  const resumeAfter = listenerApi.getState().realtime.lastEventId;
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    listenerApi.dispatch(startRealtimeStream({ streamUrl, after: resumeAfter }));
  }, reconnectDelayMs(reconnectAttempts));
}

function closeCurrentStream(dispatch) {
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  setRealtimeClientReady(false);
  if (eventSource) {
    eventSource.close();
    eventSource = null;
    activeStreamUrl = null;
    dispatch(realtimeDisconnected());
  }
}

realtimeListenerMiddleware.startListening({
  actionCreator: startRealtimeStream,
  effect: async (action, listenerApi) => {
    const { streamUrl, force = false, after = null } = action.payload || {};
    if (!streamUrl || (!force && activeStreamUrl === streamUrl)) {
      return;
    }

    closeCurrentStream(listenerApi.dispatch);
    if (force) holdPendingForRecovery(listenerApi);
    listenerApi.dispatch(realtimeConnecting());

    const source = new EventSource(withQuery(streamUrl, {
      client_id: realtimeClientId,
      ...(after ? { after } : {}),
    }));
    eventSource = source;
    activeStreamUrl = streamUrl;
    let lastEventAt = Date.now();

    const watchdog = setInterval(() => {
      if (eventSource !== source) {
        clearInterval(watchdog);
        return;
      }
      if (Date.now() - lastEventAt <= HEARTBEAT_STALE_MS) {
        return;
      }
      // Deaf stream: tear it down and reconnect. A new EventSource cannot send
      // Last-Event-ID, so resume from the last envelope explicitly; otherwise
      // a terminal broadcast during the gap is never replayed.
      clearInterval(watchdog);
      const resumeAfter = listenerApi.getState().realtime.lastEventId;
      source.close();
      setRealtimeClientReady(false);
      eventSource = null;
      activeStreamUrl = null;
      const error = 'Realtime stream stale — reconnecting';
      listenerApi.dispatch(realtimeErrored(error));
      holdPendingForRecovery(listenerApi);
      listenerApi.dispatch(startRealtimeStream({ streamUrl, after: resumeAfter }));
    }, WATCHDOG_POLL_MS);

    source.onopen = () => {
      if (eventSource !== source) {
        return;
      }
      lastEventAt = Date.now();
      listenerApi.dispatch(realtimeConnected());
    };

    source.onerror = () => {
      if (eventSource !== source) {
        return;
      }
      const error = 'Realtime stream disconnected';
      setRealtimeClientReady(false);
      listenerApi.dispatch(realtimeErrored(error));
      // Requests waiting for an event from the failed connection are kept and
      // recovered once the stream is ready again, within a deadline.
      holdPendingForRecovery(listenerApi);
      // After a network error the browser retries by itself. After a bad HTTP
      // response (a proxy's 502 while the backend restarts) it gives up for good
      // and the stream stays dead until the stale watchdog notices 45 s later,
      // so retry here, with the same backoff.
      if (source.readyState === EVENT_SOURCE_CLOSED) {
        reconnectAfterBackoff(listenerApi, source, watchdog, streamUrl, error);
      }
    };

    source.addEventListener(UI_SSE_EVENT, async (messageEvent) => {
      if (eventSource !== source) return;
      lastEventAt = Date.now();
      try {
        const event = JSON.parse(messageEvent.data);
        const invalidReason = invalidEnvelopeReason(event);
        if (invalidReason) {
          listenerApi.dispatch(realtimeErrored(`Malformed ui.event: ${invalidReason}`));
          return;
        }
        const { realtime } = listenerApi.getState();
        if (realtime.receivedIds.includes(event.id)) {
          return;
        }
        // The server's new connection has no topic interests. Register every
        // hold before stream readiness permits finite snapshots or resync.
        if (event.type === UI_EVENT.CONNECTION_READY) {
          connectedAt = Date.now();
          setRealtimeClientReady(false);
          const topicsReady = registerHeldTopics({ dispatch: listenerApi.dispatch, streamUrl });
          if (topicsReady) await topicsReady;
          if (eventSource !== source) return;
          setRealtimeClientReady(true);
          listenerApi.dispatch(realtimeStreamReady());
        }
        listenerApi.dispatch(applyUiEvent(event));
        listenerApi.dispatch(uiEventReceived(event));
        const settledRequestId = event.payload?.request_id;
        if (settledRequestId && !listenerApi.getState().realtime.pendingOps?.[settledRequestId]) {
          forgetRequest(settledRequestId);
        }
        publishUiEvent(event);
        if (event.type === UI_EVENT.CONNECTION_READY) {
          notifyStreamReady({ dispatch: listenerApi.dispatch, streamUrl });
          recoverOnReady(listenerApi, streamUrl, event.payload?.boot_id).catch(() => {});
        }
        if (event.type === UI_EVENT.CONNECTION_SHUTDOWN) {
          // The server is going away and will close this stream. Reconnecting at
          // once would land on a draining server (a ready followed by another
          // shutdown), so stop here and come back after a backoff.
          reconnectAfterBackoff(listenerApi, source, watchdog, streamUrl, 'Server is shutting down — reconnecting');
        }
      } catch (error) {
        listenerApi.dispatch(realtimeErrored(`Malformed ui.event: ${error.message}`));
      }
    });
  },
});

realtimeListenerMiddleware.startListening({
  actionCreator: stopRealtimeStream,
  effect: async (_action, listenerApi) => {
    closeCurrentStream(listenerApi.dispatch);
    reconnectAttempts = 0;
    connectedAt = 0;
    // An explicit stop (idle pause, teardown) leaves nothing to recover.
    cancelRecovery();
    failPendingOperations(listenerApi, 'Stream disconnected');
  },
});
// Register at thunk dispatch, before its fetch begins. A backend publishes the
// causal event before responding 202, so waiting for fulfilled would lose the race.
// Rejected operations fail immediately; fulfilled acknowledgements register the
// event types that are allowed to resolve the pending request.
realtimeListenerMiddleware.startListening({
  matcher: (action) => (action.type.startsWith('uiData/') || action.type.startsWith('realtime/'))
    && (isPending(action) || isRejected(action) || isFulfilled(action)),
  effect: (action, listenerApi) => {
    const requestId = requestIdFor(action);
    if (isPending(action)) {
      if (requestId) {
        noteRequestStarted(requestId);
        listenerApi.dispatch(registerPendingOp({ requestId }));
      }
      return;
    }
    if (isRejected(action)) {
      if (requestId) {
        forgetRequest(requestId);
        listenerApi.dispatch(failPendingOp({ requestId, error: action.error?.message }));
      }
      return;
    }

    const acknowledgement = acknowledgementFor(action);
    if (!acknowledgement) {
      return;
    }
    if (consumeStaleAcknowledgement(acknowledgement.request_id)) {
      listenerApi.dispatch(failPendingOp({
        requestId: acknowledgement.request_id,
        error: 'The server restarted before this request finished. Check its result, then try again.',
      }));
      return;
    }
    if (requestId && requestId !== acknowledgement.request_id) {
      listenerApi.dispatch(resolvePendingOp({ requestId }));
    }
    listenerApi.dispatch(registerPendingOp({
      requestId: acknowledgement.request_id,
      eventTypes: acknowledgement.event_types,
    }));
    noteAcknowledged(listenerApi, acknowledgement.request_id);
  },
});
