import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { configureStore } from '@reduxjs/toolkit';
import realtimeReducer, { registerPendingOp } from '@frontend/realtime/realtimeSlice';
import uiDataReducer from '@frontend/store/uiDataSlice';
import {
  realtimeListenerMiddleware,
  startRealtimeStream,
  stopRealtimeStream,
} from '@frontend/realtime/realtimeListener';
import { RECOVERY_DEADLINE_MS, resetRequestRecoveryForTest } from '@frontend/realtime/requestRecovery';
import { realtimeClientId } from '@frontend/realtime/realtimeClient';

const STREAM_URL = '/api/ui/stream';
const REPLY_TYPE = 'agents.snapshot';

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
    this.listeners.set(type, [...(this.listeners.get(type) || []), listener]);
  }

  close() {
    this.closed = true;
    this.readyState = 2;
  }

  emit(type, data) {
    for (const listener of this.listeners.get(type) || []) listener({ data });
  }
}

let seq = 0;
const envelope = (type, payload = {}) => {
  seq += 1;
  return JSON.stringify({ id: `ui-${seq}`, seq, type, scope: 'global', payload });
};

function buildStore() {
  return configureStore({
    reducer: { realtime: realtimeReducer, uiData: uiDataReducer },
    middleware: (getDefaultMiddleware) =>
      getDefaultMiddleware({ serializableCheck: false, immutableCheck: false })
        .prepend(realtimeListenerMiddleware.middleware),
  });
}

describe('recovering requests the stream dropped before they were answered', () => {
  let store;
  let resolveReply;

  const pendingOps = () => store.getState().realtime.pendingOps;
  const lastSource = () => MockEventSource.instances.at(-1);
  const ready = (source, bootId) => source.emit('ui.event', envelope('connection.ready', { stream: 'ui', boot_id: bootId }));
  const accept = (requestId) => store.dispatch(registerPendingOp({ requestId, eventTypes: [REPLY_TYPE] }));
  const reply = (source, requestId) => source.emit('ui.event', envelope(REPLY_TYPE, { request_id: requestId, agents: [] }));
  const resolveBody = (call = 0) => JSON.parse(globalThis.fetch.mock.calls[call][1].body);

  // Connect on boot `bootId`, then drop the stream and reconnect to `nextBoot`.
  async function dropAndReconnect({ before = () => {}, nextBoot = 'boot-1' } = {}) {
    store.dispatch(startRealtimeStream({ streamUrl: STREAM_URL }));
    await Promise.resolve();
    const first = lastSource();
    ready(first, 'boot-1');
    before();
    first.readyState = 2;
    first.onerror();
    await vi.advanceTimersByTimeAsync(3000);
    const second = lastSource();
    expect(second).not.toBe(first);
    ready(second, nextBoot);
    await vi.advanceTimersByTimeAsync(0);
    return second;
  }

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] });
    MockEventSource.instances = [];
    vi.stubGlobal('EventSource', MockEventSource);
    resolveReply = { boot_id: 'boot-1', replayed: [], unknown: [], lost: [] };
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => resolveReply })));
    resetRequestRecoveryForTest();
    store = buildStore();
  });

  afterEach(async () => {
    store.dispatch(stopRealtimeStream());
    await Promise.resolve();
    vi.clearAllTimers();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('keeps an accepted request through the drop, asks the server for its reply, and settles on the replayed event', async () => {
    resolveReply = { boot_id: 'boot-1', replayed: ['r1'], unknown: [], lost: [] };
    const second = await dropAndReconnect({ before: () => accept('r1') });

    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    expect(globalThis.fetch.mock.calls[0][0]).toBe(`${STREAM_URL}/resolve`);
    expect(resolveBody()).toEqual({ client_id: realtimeClientId, request_ids: ['r1'] });
    expect(pendingOps().r1).toBeDefined();

    reply(second, 'r1');
    expect(pendingOps().r1).toBeUndefined();
  });

  it('does not ask for anything on the first connection or when nothing is pending', async () => {
    store.dispatch(startRealtimeStream({ streamUrl: STREAM_URL }));
    await Promise.resolve();
    store.dispatch(registerPendingOp({ requestId: 'early' }));
    ready(lastSource(), 'boot-1');
    await vi.advanceTimersByTimeAsync(0);
    expect(globalThis.fetch).not.toHaveBeenCalled();

    const calls = globalThis.fetch.mock.calls.length;
    store.dispatch(stopRealtimeStream());
    await Promise.resolve();
    await dropAndReconnect();
    expect(globalThis.fetch.mock.calls.length).toBe(calls);
  });

  it('fails an accepted request when the server restarted, but leaves one that has not been sent yet', async () => {
    await dropAndReconnect({
      before: () => {
        accept('accepted');
        store.dispatch(registerPendingOp({ requestId: 'unsent' }));
      },
      nextBoot: 'boot-2',
    });

    expect(globalThis.fetch).not.toHaveBeenCalled();
    expect(pendingOps().accepted).toBeUndefined();
    expect(store.getState().realtime.error).toMatch(/server restarted/i);
    expect(pendingOps().unsent).toBeDefined();
  });

  it('fails a reply that was too large to keep straight away', async () => {
    resolveReply = { boot_id: 'boot-1', replayed: [], unknown: [], lost: ['big'] };
    await dropAndReconnect({ before: () => accept('big') });

    expect(pendingOps().big).toBeUndefined();
    expect(store.getState().realtime.error).toMatch(/too large/i);
  });

  it('waits for a request the server has not answered yet, settles it if the reply comes live, and gives up at the deadline', async () => {
    resolveReply = { boot_id: 'boot-1', replayed: [], unknown: ['slow', 'never'], lost: [] };
    const second = await dropAndReconnect({ before: () => { accept('slow'); accept('never'); } });

    expect(pendingOps().slow).toBeDefined();
    for (let elapsed = 0; elapsed < RECOVERY_DEADLINE_MS - 4000; elapsed += 15000) {
      await vi.advanceTimersByTimeAsync(Math.min(15000, RECOVERY_DEADLINE_MS - 4000 - elapsed));
      second.emit('ui.event', envelope('connection.heartbeat'));
    }
    reply(second, 'slow');
    expect(pendingOps().slow).toBeUndefined();
    expect(pendingOps().never).toBeDefined();

    await vi.advanceTimersByTimeAsync(1000);
    expect(pendingOps().never).toBeUndefined();
    expect(store.getState().realtime.error).toMatch(/connection lost/i);
  });

  it('asks for a request as soon as it is accepted when it was still being sent as the stream dropped', async () => {
    store.dispatch(startRealtimeStream({ streamUrl: STREAM_URL }));
    await Promise.resolve();
    const first = lastSource();
    ready(first, 'boot-1');
    store.dispatch(registerPendingOp({ requestId: 'inflight' }));
    first.readyState = 2;
    first.onerror();
    await vi.advanceTimersByTimeAsync(3000);
    ready(lastSource(), 'boot-1');
    await vi.advanceTimersByTimeAsync(0);
    expect(globalThis.fetch).not.toHaveBeenCalled();

    store.dispatch({
      type: 'uiData/anything/fulfilled',
      payload: { request_id: 'inflight', event_types: [REPLY_TYPE] },
      meta: { requestId: 'inflight', requestStatus: 'fulfilled', arg: {} },
    });
    await vi.advanceTimersByTimeAsync(0);

    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    expect(resolveBody().request_ids).toEqual(['inflight']);
  });

  it('a failed resolve call leaves the request pending until the deadline', async () => {
    globalThis.fetch.mockRejectedValue(new Error('offline'));
    await dropAndReconnect({ before: () => accept('r1') });

    expect(pendingOps().r1).toBeDefined();
    await vi.advanceTimersByTimeAsync(RECOVERY_DEADLINE_MS);
    expect(pendingOps().r1).toBeUndefined();
  });

  it('keeps an individual deadline while resolve is stalled', async () => {
    globalThis.fetch.mockImplementation(() => new Promise(() => {}));
    await dropAndReconnect({ before: () => accept('stalled') });

    expect(pendingOps().stalled).toBeDefined();
    await vi.advanceTimersByTimeAsync(RECOVERY_DEADLINE_MS);
    expect(pendingOps().stalled).toBeUndefined();
  });

  it('rejects a delayed acknowledgement from the server boot that restarted', async () => {
    store.dispatch(startRealtimeStream({ streamUrl: STREAM_URL }));
    await Promise.resolve();
    ready(lastSource(), 'boot-1');
    store.dispatch({
      type: 'uiData/anything/pending',
      meta: { requestId: 'late', requestStatus: 'pending', arg: {} },
    });
    await Promise.resolve();
    lastSource().readyState = 2;
    lastSource().onerror();
    await vi.advanceTimersByTimeAsync(3000);
    ready(lastSource(), 'boot-2');
    await vi.advanceTimersByTimeAsync(0);

    store.dispatch({
      type: 'uiData/anything/fulfilled',
      payload: { request_id: 'late', event_types: [REPLY_TYPE] },
      meta: { requestId: 'late', requestStatus: 'fulfilled', arg: {} },
    });

    expect(pendingOps().late).toBeUndefined();
    expect(store.getState().realtime.error).toMatch(/server restarted/i);
  });

  it('gives up on a request when the stream never comes back', async () => {
    store.dispatch(startRealtimeStream({ streamUrl: STREAM_URL }));
    await Promise.resolve();
    const first = lastSource();
    ready(first, 'boot-1');
    accept('r1');
    first.readyState = 2;
    first.onerror();

    await vi.advanceTimersByTimeAsync(RECOVERY_DEADLINE_MS - 1000);
    expect(pendingOps().r1).toBeDefined();
    await vi.advanceTimersByTimeAsync(1000);
    expect(pendingOps().r1).toBeUndefined();
    expect(store.getState().realtime.error).toMatch(/connection lost/i);
  });

  it('an explicit stop fails what is pending and leaves no timer to fire later', async () => {
    store.dispatch(startRealtimeStream({ streamUrl: STREAM_URL }));
    await Promise.resolve();
    const first = lastSource();
    ready(first, 'boot-1');
    accept('r1');
    first.readyState = 2;
    first.onerror();

    store.dispatch(stopRealtimeStream());
    await Promise.resolve();
    expect(pendingOps().r1).toBeUndefined();

    store.dispatch(registerPendingOp({ requestId: 'later', eventTypes: [REPLY_TYPE] }));
    await vi.advanceTimersByTimeAsync(RECOVERY_DEADLINE_MS * 2);
    expect(pendingOps().later).toBeDefined();
  });

  it('asks for at most 64 requests per call', async () => {
    const ids = Array.from({ length: 70 }, (_, index) => `r${index}`);
    await dropAndReconnect({ before: () => ids.forEach(accept) });

    expect(globalThis.fetch).toHaveBeenCalledTimes(2);
    expect(resolveBody(0).request_ids).toHaveLength(64);
    expect(resolveBody(1).request_ids).toHaveLength(6);
  });
});
