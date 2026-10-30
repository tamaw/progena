import { UI_EVENT } from './eventVocabulary';
import { createSlice, original } from '@reduxjs/toolkit';

const MAX_RECEIVED_EVENTS_PER_REQUEST = 100;
const MAX_EVENT_QUEUE = 500;
const MAX_RECEIVED_IDS = 300;

const initialState = {
  connected: false,
  // `connected` follows EventSource.onopen, which is only "headers arrived".
  // The backend registers our subscriber queue inside the stream generator and
  // announces it with `connection.ready`, so that event -- not onopen -- is
  // the point from which a broadcast can actually reach us. Anything whose
  // only reply is a one-shot ui.event has to wait for this.
  streamReady: false,
  status: 'idle',
  lastEventId: null,
  lastEventAt: null,
  lastEvent: null,
  // Ordered canonical frames for foreground consumers. A queue, rather than a
  // single last-event slot, preserves token bursts and request-correlated
  // events followed immediately by a snapshot.
  eventQueue: [],
  eventSequence: 0,
  error: null,
  receivedIds: [],
  // Pending operations awaiting their causal ui.event — keyed by request_id
  pendingOps: {},
  // Request-correlated events seen recently. This allows a causal stream frame
  // that arrives before its HTTP acknowledgement to be reconciled when the
  // acknowledgement later declares its event types.
  receivedRequestEvents: {},
};

function isUiEventEnvelope(event) {
  return Boolean(
    event
    && typeof event === 'object'
    && !Array.isArray(event)
    && typeof event.id === 'string'
    && event.id
    && typeof event.type === 'string'
    && event.type
    && event.payload
    && typeof event.payload === 'object'
    && !Array.isArray(event.payload),
  );
}

function hasMatchingReceivedEvent(state, requestId, eventTypes) {
  if (!requestId || !Array.isArray(eventTypes) || eventTypes.length === 0) {
    return false;
  }
  return (state.receivedRequestEvents[requestId] || []).some((event) => (
    eventTypes.includes(event.type)
  ));
}

const realtimeSlice = createSlice({
  name: 'realtime',
  initialState,
  reducers: {
    realtimeConnecting(state) {
      state.status = 'connecting';
      state.streamReady = false;
      state.error = null;
    },
    realtimeConnected(state) {
      state.connected = true;
      state.status = 'connected';
      state.error = null;
      // A fresh connection (initial or an automatic reconnect) may be to a
      // different backend process, whose event-id sequence restarts from 1 -
      // stale ids from a prior process's lifetime must not dedupe-suppress
      // genuinely new events that happen to reuse the same low ids.
      state.receivedIds = [];
    },
    // The backend has us in its subscriber set; broadcasts can reach us now.
    realtimeStreamReady(state) {
      state.streamReady = true;
    },
    realtimeDisconnected(state) {
      state.connected = false;
      state.streamReady = false;
      state.status = 'disconnected';
    },
    realtimeErrored(state, action) {
      state.connected = false;
      state.streamReady = false;
      state.status = 'error';
      state.error = action.payload || 'Realtime stream failed';
    },
    uiEventReceived(state, action) {
      const event = action.payload;
      const knownIds = original(state.receivedIds);
      if (!isUiEventEnvelope(event) || knownIds.includes(event.id)) {
        return;
      }
      state.lastEventId = event.id;
      state.lastEventAt = event.timestamp || new Date().toISOString();
      if (event.type !== UI_EVENT.CONNECTION_HEARTBEAT && event.type !== UI_EVENT.STATUS_HEARTBEAT) {
        state.lastEvent = event;
      }
      state.receivedIds = [...knownIds.slice(-(MAX_RECEIVED_IDS - 1)), event.id];
      state.eventSequence += 1;
      const entry = { sequence: state.eventSequence, event };
      // Rebuild from the frozen base: splicing the draft makes Immer proxy all
      // 500 retained entries on every event once the buffer is full.
      state.eventQueue = [...original(state.eventQueue).slice(-(MAX_EVENT_QUEUE - 1)), entry];
      // Keep a bounded request-correlated record even when an event wins the
      // race with its 202 acknowledgement. The acknowledgement is reconciled
      // against this record by registerPendingOp.
      const requestId = event?.payload?.request_id;
      if (requestId) {
        const received = state.receivedRequestEvents[requestId] || [];
        if (!received.some((receivedEvent) => receivedEvent.id === event.id)) {
          received.push({ id: event.id, type: event.type });
        }
        if (received.length > MAX_RECEIVED_EVENTS_PER_REQUEST) {
          received.splice(0, received.length - MAX_RECEIVED_EVENTS_PER_REQUEST);
        }
        state.receivedRequestEvents[requestId] = received;
        const requestIds = Object.keys(state.receivedRequestEvents);
        if (requestIds.length > 300) {
          delete state.receivedRequestEvents[requestIds[0]];
        }
      }

      // Resolve only the operation with the same request_id and one of the
      // event types that its acknowledgement declared.
      const pendingOp = requestId ? state.pendingOps[requestId] : null;
      if (pendingOp && event.type && pendingOp.eventTypes.includes(event.type)) {
        delete state.pendingOps[requestId];
      }
    },
    registerPendingOp(state, action) {
      const { requestId, eventTypes } = action.payload;
      if (!requestId) return;

      const acknowledged = Array.isArray(eventTypes);
      const normalizedEventTypes = acknowledged
        ? eventTypes.filter((eventType) => typeof eventType === 'string' && eventType)
        : state.pendingOps[requestId]?.eventTypes || [];

      // A valid acknowledgement must declare at least one causal event. The
      // HTTP client rejects malformed acknowledgements; do not persist one if a
      // caller dispatches an invalid acknowledgement action directly.
      if (acknowledged && normalizedEventTypes.length === 0) {
        delete state.pendingOps[requestId];
        return;
      }

      // The event may have reached EventSource before the 202 response. Do
      // not recreate a pending operation after that causal event was applied.
      if (hasMatchingReceivedEvent(state, requestId, normalizedEventTypes)) {
        delete state.pendingOps[requestId];
        return;
      }

      const existing = state.pendingOps[requestId];
      state.pendingOps[requestId] = {
        requestId,
        eventTypes: normalizedEventTypes,
        status: acknowledged ? 'pending' : 'awaiting_acknowledgement',
        timestamp: existing?.timestamp || Date.now(),
        error: null,
      };
    },
    resolvePendingOp(state, action) {
      const { requestId } = action.payload;
      delete state.pendingOps[requestId];
    },
    failPendingOp(state, action) {
      const { requestId, error } = action.payload;
      if (state.pendingOps[requestId]) {
        delete state.pendingOps[requestId];
      }
      state.error = error || 'Request failed';
    },
  },
});

export const {
  realtimeConnecting,
  realtimeConnected,
  realtimeStreamReady,
  realtimeDisconnected,
  realtimeErrored,
  uiEventReceived,
  registerPendingOp,
  resolvePendingOp,
  failPendingOp,
} = realtimeSlice.actions;

export const selectRealtime = (state) => state.realtime;
export const selectRealtimeConnected = (state) => state.realtime.connected;
export const selectRealtimeStreamReady = (state) => state.realtime.streamReady;
export const selectLastUiEvent = (state) => state.realtime.lastEvent;
export const selectUiEventQueue = (state) => state.realtime.eventQueue;
export const selectPendingOp = (state, requestId) =>
  requestId ? state.realtime.pendingOps[requestId] || null : null;
export const selectHasPendingOp = (state) => (
  Object.values(state.realtime.pendingOps).some((operation) => (
    operation.status === 'awaiting_acknowledgement' || operation.status === 'pending'
  ))
);

export default realtimeSlice.reducer;
