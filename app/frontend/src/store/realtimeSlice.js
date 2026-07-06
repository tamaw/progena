import { createSlice } from '@reduxjs/toolkit';

const initialState = {
  connected: false,
  status: 'idle',
  lastEventId: null,
  lastEventAt: null,
  lastEvent: null,
  error: null,
  receivedIds: [],
};

const realtimeSlice = createSlice({
  name: 'realtime',
  initialState,
  reducers: {
    realtimeConnecting(state) {
      state.status = 'connecting';
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
    realtimeDisconnected(state) {
      state.connected = false;
      state.status = 'disconnected';
    },
    realtimeErrored(state, action) {
      state.connected = false;
      state.status = 'error';
      state.error = action.payload || 'Realtime stream failed';
    },
    uiEventReceived(state, action) {
      const event = action.payload;
      if (!event?.id || state.receivedIds.includes(event.id)) {
        return;
      }
      state.lastEventId = event.id;
      state.lastEventAt = event.timestamp || new Date().toISOString();
      if (event.type !== 'connection.heartbeat' && event.type !== 'status.heartbeat') {
        state.lastEvent = event;
      }
      state.receivedIds.push(event.id);
      if (state.receivedIds.length > 300) {
        state.receivedIds.splice(0, state.receivedIds.length - 300);
      }
    },
  },
});

export const {
  realtimeConnecting,
  realtimeConnected,
  realtimeDisconnected,
  realtimeErrored,
  uiEventReceived,
} = realtimeSlice.actions;

export const selectRealtime = (state) => state.realtime;
export const selectLastUiEvent = (state) => state.realtime.lastEvent;

export default realtimeSlice.reducer;
