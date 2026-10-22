import { realtimeClientId } from './realtimeClient';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { configureStore } from '@reduxjs/toolkit';
import realtimeReducer, { registerPendingOp } from './realtimeSlice';
import uiDataReducer from '../store/uiDataSlice';
import { subscribeUiEvent } from './realtimeEvents';
import {
  realtimeListenerMiddleware,
  startRealtimeStream,
  stopRealtimeStream,
} from './realtimeListener';

const STREAM_URL = '/api/ui/stream';

// Backend cadence: connection.heartbeat every 15s; the middleware treats a
// stream silent for more than 45s as deaf and reconnects (values mirrored
// from realtimeListener.js HEARTBEAT_STALE_MS / WATCHDOG_POLL_MS).
const HEARTBEAT_INTERVAL_MS = 15000;

class MockEventSource {
  static instances = [];

  constructor(url) {
    this.url = url;
    this.closed = false;
    this.onopen = null;
    this.onerror = null;
    this.listeners = new Map();
    MockEventSource.instances.push(this);
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  close() {
    this.closed = true;
  }

  emit(type, data) {
    for (const listener of this.listeners.get(type) || []) {
      listener({ data });
    }
  }
}

function buildStore() {
  // Mirrors store.js: same reducers, listener middleware prepended.
  return configureStore({
    reducer: {
      realtime: realtimeReducer,
      uiData: uiDataReducer,
    },
    middleware: (getDefaultMiddleware) =>
      getDefaultMiddleware().prepend(realtimeListenerMiddleware.middleware),
  });
}

let envelopeSeq = 0;
function heartbeatEnvelope() {
  envelopeSeq += 1;
  return JSON.stringify({
    id: `ui-${envelopeSeq}`,
    seq: envelopeSeq,
    type: 'connection.heartbeat',
    scope: 'global',
    payload: {},
  });
}

describe('realtimeListener heartbeat watchdog', () => {
  let store;

  beforeEach(() => {
    vi.useFakeTimers({
      toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'],
    });
    MockEventSource.instances = [];
    vi.stubGlobal('EventSource', MockEventSource);
    store = buildStore();
  });

  afterEach(async () => {
    // Reset the module-level eventSource/activeStreamUrl singletons so the
    // next test's startRealtimeStream is not ignored as a duplicate URL.
    store.dispatch(stopRealtimeStream());
    await Promise.resolve();
    vi.clearAllTimers();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('closes a deaf stream and reconnects to the same url when no event arrives', async () => {
    store.dispatch(startRealtimeStream({ streamUrl: STREAM_URL }));
    await Promise.resolve();

    expect(MockEventSource.instances).toHaveLength(1);
    const first = MockEventSource.instances[0];
    expect(first.url).toBe(`${STREAM_URL}?client_id=${realtimeClientId}`);

    // A request left waiting on the dead connection must not pend forever.
    store.dispatch(registerPendingOp({ requestId: 'req-stale-1' }));
    expect(store.getState().realtime.pendingOps['req-stale-1']).toBeDefined();

    // 45s of silence is still tolerated (heartbeats can be one poll late).
    await vi.advanceTimersByTimeAsync(45000);
    expect(first.closed).toBe(false);
    expect(MockEventSource.instances).toHaveLength(1);

    // Past HEARTBEAT_STALE_MS the watchdog tears the stream down and
    // reconnects from scratch to the same url.
    await vi.advanceTimersByTimeAsync(15000);
    expect(first.closed).toBe(true);
    expect(MockEventSource.instances).toHaveLength(2);
    const second = MockEventSource.instances[1];
    expect(second.url).toBe(`${STREAM_URL}?client_id=${realtimeClientId}`);
    expect(second.closed).toBe(false);

    // The pending operation was failed rather than left dangling, and the
    // fresh connection attempt is underway.
    expect(store.getState().realtime.pendingOps['req-stale-1']).toBeUndefined();
    expect(store.getState().realtime.status).toBe('connecting');
  });

  it('keeps a single stream alive while heartbeats keep arriving', async () => {
    store.dispatch(startRealtimeStream({ streamUrl: STREAM_URL }));
    await Promise.resolve();

    expect(MockEventSource.instances).toHaveLength(1);
    const source = MockEventSource.instances[0];

    // 90s with a connection.heartbeat every 15s: every received ui.event —
    // heartbeats included — resets the staleness clock.
    for (let tick = 0; tick < 6; tick += 1) {
      await vi.advanceTimersByTimeAsync(HEARTBEAT_INTERVAL_MS);
      source.emit('ui.event', heartbeatEnvelope());
    }

    expect(MockEventSource.instances).toHaveLength(1);
    expect(source.closed).toBe(false);
  });

  it('forces a fresh connection and ignores late frames from the replaced stream', async () => {
    store.dispatch(startRealtimeStream({ streamUrl: STREAM_URL }));
    const first = MockEventSource.instances[0];
    store.dispatch(registerPendingOp({ requestId: 'replaced-request' }));
    store.dispatch(startRealtimeStream({ streamUrl: STREAM_URL, force: true }));
    const second = MockEventSource.instances[1];

    expect(first.closed).toBe(true);
    expect(second.closed).toBe(false);
    expect(store.getState().realtime.pendingOps['replaced-request']).toBeUndefined();
    const stale = { id: 'stale-status', type: 'status.updated', payload: { status: 'stale' } };
    first.emit('ui.event', JSON.stringify(stale));
    expect(store.getState().realtime.eventQueue).toEqual([]);
    const current = { id: 'current-status', type: 'status.updated', payload: { status: 'healthy' } };
    second.emit('ui.event', JSON.stringify(current));
    expect(store.getState().realtime.eventQueue).toEqual([{ sequence: 1, event: current }]);
    expect(store.getState().uiData.status.status).toBe('healthy');
  });

  it('routes canonical response lifecycle envelopes through Redux and publishUiEvent', async () => {
    store.dispatch(startRealtimeStream({ streamUrl: STREAM_URL }));
    await Promise.resolve();

    const source = MockEventSource.instances[0];
    const publishedEvents = [];
    const unsubscribe = subscribeUiEvent((event) => publishedEvents.push(event));
    const lifecycleEvents = [
      {
        id: 'response-started-1',
        seq: 1,
        type: 'chat.response.started',
        scope: 'chat',
        payload: { request_id: 'request-lifecycle', thread_id: 'thread-background' },
      },
      {
        id: 'response-completed-2',
        seq: 2,
        type: 'chat.response.completed',
        scope: 'chat',
        payload: { request_id: 'request-lifecycle', thread_id: 'thread-background' },
      },
    ];

    for (const event of lifecycleEvents) {
      source.emit('ui.event', JSON.stringify(event));
    }
    unsubscribe();

    expect(publishedEvents).toEqual(lifecycleEvents);
    expect(store.getState().realtime).toMatchObject({
      eventSequence: 2,
      receivedIds: lifecycleEvents.map((event) => event.id),
      lastEvent: lifecycleEvents[1],
      eventQueue: lifecycleEvents.map((event, index) => ({ sequence: index + 1, event })),
    });
  });
});
