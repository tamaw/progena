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
import { withQuery } from '../store/http';
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

function isUiDataThunkLifecycle(action) {
  return action.type.startsWith('uiData/');
}

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

function closeCurrentStream(dispatch) {
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
    const { streamUrl, force = false } = action.payload || {};
    if (!streamUrl || (!force && activeStreamUrl === streamUrl)) {
      return;
    }

    closeCurrentStream(listenerApi.dispatch);
    if (force) failPendingOperations(listenerApi, 'Reconnecting to event stream');
    listenerApi.dispatch(realtimeConnecting());

    const source = new EventSource(withQuery(streamUrl, { client_id: realtimeClientId }));
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
      // Deaf stream: tear it down and reconnect from scratch.
      clearInterval(watchdog);
      source.close();
      setRealtimeClientReady(false);
      eventSource = null;
      activeStreamUrl = null;
      const error = 'Realtime stream stale — reconnecting';
      listenerApi.dispatch(realtimeErrored(error));
      failPendingOperations(listenerApi, error);
      listenerApi.dispatch(startRealtimeStream({ streamUrl }));
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
      // EventSource retries automatically after an error. Requests waiting for
      // an event from the failed connection must not remain pending forever.
      failPendingOperations(listenerApi, error);
    };

    source.addEventListener('ui.event', (messageEvent) => {
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
        // `connection.ready` is emitted from inside the stream generator, so
        // by the time it lands the backend has our subscriber queue and a
        // broadcast can no longer be published into the void.
        if (event.type === 'connection.ready') {
          setRealtimeClientReady(true);
          listenerApi.dispatch(realtimeStreamReady());
        }
        listenerApi.dispatch(applyUiEvent(event));
        listenerApi.dispatch(uiEventReceived(event));
        publishUiEvent(event);
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
    // Fail all pending operations when the stream disconnects.
    failPendingOperations(listenerApi, 'Stream disconnected');
  },
});
// Register at thunk dispatch, before its fetch begins. A backend publishes the
// causal event before responding 202, so waiting for fulfilled would lose the race.
// Rejected operations fail immediately; fulfilled acknowledgements register the
// event types that are allowed to resolve the pending request.
realtimeListenerMiddleware.startListening({
  matcher: (action) => isUiDataThunkLifecycle(action)
    && (isPending(action) || isRejected(action) || isFulfilled(action)),
  effect: (action, listenerApi) => {
    const requestId = requestIdFor(action);
    if (isPending(action)) {
      if (requestId) {
        listenerApi.dispatch(registerPendingOp({ requestId }));
      }
      return;
    }
    if (isRejected(action)) {
      if (requestId) {
        listenerApi.dispatch(failPendingOp({ requestId, error: action.error?.message }));
      }
      return;
    }

    const acknowledgement = acknowledgementFor(action);
    if (!acknowledgement) {
      return;
    }
    if (requestId && requestId !== acknowledgement.request_id) {
      listenerApi.dispatch(resolvePendingOp({ requestId }));
    }
    listenerApi.dispatch(registerPendingOp({
      requestId: acknowledgement.request_id,
      eventTypes: acknowledgement.event_types,
    }));
  },
});
