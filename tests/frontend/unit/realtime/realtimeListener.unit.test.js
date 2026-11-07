import { realtimeClientId } from '@frontend/realtime/realtimeClient';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { configureStore } from '@reduxjs/toolkit';
import realtimeReducer, { registerPendingOp } from '@frontend/realtime/realtimeSlice';
import uiDataReducer from '@frontend/store/uiDataSlice';
import { subscribeUiEvent } from '@frontend/realtime/realtimeEvents';
import {
  realtimeListenerMiddleware,
  reconnectDelayMs,
  startRealtimeStream,
  stopRealtimeStream,
} from '@frontend/realtime/realtimeListener';
import { onStreamReady } from '@frontend/realtime/streamLifecycle';
import { RECOVERY_DEADLINE_MS } from '@frontend/realtime/requestRecovery';

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
    this.readyState = 0;
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
    this.readyState = 2;
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

    // The pending request is kept for recovery, not failed, while the fresh
    // connection attempt is underway; it still cannot wait forever.
    expect(store.getState().realtime.pendingOps['req-stale-1']).toBeDefined();
    expect(store.getState().realtime.status).toBe('connecting');
    await vi.advanceTimersByTimeAsync(RECOVERY_DEADLINE_MS);
    expect(store.getState().realtime.pendingOps['req-stale-1']).toBeUndefined();
  });

  it('resumes a deaf stream after its last envelope so missed terminals are replayed', async () => {
    store.dispatch(startRealtimeStream({ streamUrl: STREAM_URL }));
    await Promise.resolve();
    const first = MockEventSource.instances[0];
    const delivered = heartbeatEnvelope();
    first.emit('ui.event', delivered);
    const lastId = JSON.parse(delivered).id;

    await vi.advanceTimersByTimeAsync(60000);

    expect(first.closed).toBe(true);
    expect(MockEventSource.instances).toHaveLength(2);
    expect(MockEventSource.instances[1].url).toBe(
      `${STREAM_URL}?client_id=${realtimeClientId}&after=${lastId}`,
    );
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
    expect(store.getState().realtime.pendingOps['replaced-request']).toBeDefined();
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


function envelope(type, payload = {}) {
  envelopeSeq += 1;
  return JSON.stringify({ id: `ui-${envelopeSeq}`, seq: envelopeSeq, type, scope: 'global', payload });
}

describe('realtimeListener server shutdown', () => {
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
    store.dispatch(stopRealtimeStream());
    await Promise.resolve();
    vi.clearAllTimers();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  const start = async () => {
    store.dispatch(startRealtimeStream({ streamUrl: STREAM_URL }));
    await Promise.resolve();
    return MockEventSource.instances.at(-1);
  };
  const shutdown = (source) => source.emit('ui.event', envelope('connection.shutdown', { reason: 'server_shutdown' }));

  it('closes the stream, fails pending requests and waits before reconnecting', async () => {
    const first = await start();
    first.emit('ui.event', envelope('connection.ready', { stream: 'ui' }));
    store.dispatch(registerPendingOp({ requestId: 'req-1' }));
    await vi.advanceTimersByTimeAsync(11000);

    shutdown(first);

    expect(first.closed).toBe(true);
    expect(store.getState().realtime.streamReady).toBe(false);
    expect(store.getState().realtime.pendingOps['req-1']).toBeDefined();
    await vi.advanceTimersByTimeAsync(500);
    expect(MockEventSource.instances).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(MockEventSource.instances).toHaveLength(2);
  });

  it('doubles the wait while a reconnect is shut down again straight away, and resumes after the last envelope', async () => {
    const first = await start();
    first.emit('ui.event', envelope('connection.ready'));
    await vi.advanceTimersByTimeAsync(11000);
    shutdown(first);
    await vi.advanceTimersByTimeAsync(2000);
    const second = MockEventSource.instances[1];
    expect(second.url).toContain('after=ui-');

    // A draining server: ready, then shutdown with no time in between.
    second.emit('ui.event', envelope('connection.ready'));
    shutdown(second);
    await vi.advanceTimersByTimeAsync(1000);
    expect(MockEventSource.instances).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(2000);
    expect(MockEventSource.instances).toHaveLength(3);
  });

  it('reopens a source the browser gave up on (a 502 while the backend restarts) without waiting for the watchdog', async () => {
    const first = await start();
    first.emit('ui.event', envelope('connection.ready'));
    store.dispatch(registerPendingOp({ requestId: 'req-2' }));

    first.readyState = 2;
    first.onerror();

    expect(first.closed).toBe(true);
    expect(store.getState().realtime.pendingOps['req-2']).toBeDefined();
    // First retry waits 2 s ±25%.
    await vi.advanceTimersByTimeAsync(1000);
    expect(MockEventSource.instances).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1600);
    expect(MockEventSource.instances).toHaveLength(2);

    // The new attempt fails too: the wait doubles (4 s ±25%) instead of retrying at once.
    MockEventSource.instances[1].readyState = 2;
    MockEventSource.instances[1].onerror();
    await vi.advanceTimersByTimeAsync(2000);
    expect(MockEventSource.instances).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(3100);
    expect(MockEventSource.instances).toHaveLength(3);
  });

  it('leaves a source the browser is still retrying on its own', async () => {
    const first = await start();
    first.emit('ui.event', envelope('connection.ready'));

    first.readyState = 0;
    first.onerror();
    await vi.advanceTimersByTimeAsync(30000);

    expect(first.closed).toBe(false);
    expect(MockEventSource.instances).toHaveLength(1);
  });

  it('an explicit stop cancels the pending reconnect', async () => {
    const first = await start();
    first.emit('ui.event', envelope('connection.ready'));
    shutdown(first);
    store.dispatch(stopRealtimeStream());
    await Promise.resolve();

    await vi.advanceTimersByTimeAsync(30000);
    expect(MockEventSource.instances).toHaveLength(1);
  });

  it('runs stream-ready handlers on every connection.ready and flags the later ones as reconnects', async () => {
    const seen = [];
    const off = onStreamReady(({ reconnect, streamUrl }) => seen.push([reconnect, streamUrl]));
    const first = await start();
    first.emit('ui.event', envelope('connection.ready'));
    first.emit('ui.event', envelope('connection.heartbeat'));
    const second = await (async () => {
      store.dispatch(startRealtimeStream({ streamUrl: STREAM_URL, force: true }));
      await Promise.resolve();
      return MockEventSource.instances.at(-1);
    })();
    second.emit('ui.event', envelope('connection.ready'));
    off();

    expect(seen.length).toBe(2);
    expect(seen[0][1]).toBe(STREAM_URL);
    expect(seen[1][0]).toBe(true);
  });
});

describe('reconnectDelayMs', () => {
  it('doubles from one second, caps at eight and jitters by a quarter either way', () => {
    const mid = () => 0.5;
    expect([0, 1, 2, 3, 4, 9].map((n) => reconnectDelayMs(n, mid))).toEqual([1000, 2000, 4000, 8000, 8000, 8000]);
    expect(reconnectDelayMs(2, () => 0)).toBe(3000);
    expect(reconnectDelayMs(2, () => 1)).toBe(5000);
  });
});
