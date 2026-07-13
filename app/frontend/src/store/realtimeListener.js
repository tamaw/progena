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
      listenerApi.dispatch(realtimeConnected());
    };

    source.onerror = () => {
      listenerApi.dispatch(realtimeErrored('Realtime stream disconnected'));
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
    // Fail all pending operations when the stream disconnects
    const state = listenerApi.getState();
    for (const requestId of Object.keys(state.realtime.pendingOps || {})) {
      listenerApi.dispatch(failPendingOp({ requestId, error: 'Stream disconnected' }));
    }
  },
});
// Register at thunk dispatch, before its fetch begins. A backend publishes the
// causal event before responding 202, so waiting for fulfilled would lose the
// race. The reducer reconciles any event that still arrives first.
realtimeListenerMiddleware.startListening({
  matcher: (action) => isPending(action) && isUiDataThunkLifecycle(action),
  effect: (action, listenerApi) => {
    const requestId = requestIdFor(action);
    if (requestId) {
      listenerApi.dispatch(registerPendingOp({ requestId }));
    }
  },
});

realtimeListenerMiddleware.startListening({
  matcher: (action) => isRejected(action) && isUiDataThunkLifecycle(action),
  effect: (action, listenerApi) => {
    const requestId = requestIdFor(action);
    if (requestId) {
      listenerApi.dispatch(failPendingOp({ requestId, error: action.error?.message }));
    }
  },
});


// Reconcile the acknowledgement's declared causal event types. Local thunk
// metadata may live alongside the acknowledgement or under `ack`; neither is
// domain data and neither hydrates Redux.
realtimeListenerMiddleware.startListening({
  matcher: (action) => isFulfilled(action) && isUiDataThunkLifecycle(action),
  effect: (action, listenerApi) => {
    const acknowledgement = acknowledgementFor(action);
    if (!acknowledgement) {
      return;
    }
    const provisionalRequestId = requestIdFor(action);
    if (provisionalRequestId && provisionalRequestId !== acknowledgement.request_id) {
      listenerApi.dispatch(resolvePendingOp({ requestId: provisionalRequestId }));
    }
    listenerApi.dispatch(registerPendingOp({
      requestId: acknowledgement.request_id,
      eventTypes: acknowledgement.event_types,
    }));
  },
});
