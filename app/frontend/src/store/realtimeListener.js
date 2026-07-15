import { createAction, createListenerMiddleware, isFulfilled, isPending, isRejected } from '@reduxjs/toolkit';
import {
  realtimeConnected,
  realtimeConnecting,
  realtimeDisconnected,
  realtimeErrored,
  uiEventReceived,
  registerPendingOp,
  failPendingOp,
  resolvePendingOp,
} from './realtimeSlice';
import { applyUiEvent } from './uiDataSlice';
import { publishUiEvent } from './realtimeEvents';
export const startRealtimeStream = createAction('realtime/startStream');
export const stopRealtimeStream = createAction('realtime/stopStream');

export const realtimeListenerMiddleware = createListenerMiddleware();

let eventSource = null;
let activeStreamUrl = null;

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
    const { streamUrl } = action.payload || {};
    if (!streamUrl || activeStreamUrl === streamUrl) {
      return;
    }

    closeCurrentStream(listenerApi.dispatch);
    listenerApi.dispatch(realtimeConnecting());

    const source = new EventSource(streamUrl);
    eventSource = source;
    activeStreamUrl = streamUrl;

    source.onopen = () => {
      if (eventSource !== source) {
        return;
      }
      listenerApi.dispatch(realtimeConnected());
    };

    source.onerror = () => {
      if (eventSource !== source) {
        return;
      }
      const error = 'Realtime stream disconnected';
      listenerApi.dispatch(realtimeErrored(error));
      // EventSource retries automatically after an error. Requests waiting for
      // an event from the failed connection must not remain pending forever.
      failPendingOperations(listenerApi, error);
    };

    source.addEventListener('ui.event', (messageEvent) => {
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
