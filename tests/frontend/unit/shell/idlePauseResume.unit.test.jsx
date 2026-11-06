// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@frontend/context/ConfigContext', () => ({
  useConfig: () => ({
    config: { session: { idleTimeoutMinutes: 30 } },
    getApiUrl: (path) => `/api/ui/${path}`,
  }),
}));
vi.mock('@frontend/components/Modal', () => ({
  default: ({ children }) => <div role="dialog">{children}</div>,
}));

const { default: realtimeReducer } = await import('@frontend/realtime/realtimeSlice');
const { default: uiDataReducer } = await import('@frontend/store/uiDataSlice');
const { realtimeListenerMiddleware, startRealtimeStream, stopRealtimeStream } = await import('@frontend/realtime/realtimeListener');
const { default: ConnectionRecoveryModal } = await import('@frontend/shell/ConnectionRecoveryModal');
const { default: useStreamTopic } = await import('@frontend/hooks/useStreamTopic');
const { default: useStreamResync } = await import('@frontend/hooks/useStreamResync');
const { resetStreamTopicsForTest } = await import('@frontend/realtime/streamTopics');
const { resetStreamLifecycleForTest } = await import('@frontend/realtime/streamLifecycle');
const { resetRequestRecoveryForTest } = await import('@frontend/realtime/requestRecovery');

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const MINUTE = 60_000;
const STREAM_URL = '/api/ui/stream';

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

const resyncs = [];
function Page() {
  useStreamTopic('agents');
  useStreamResync((context) => resyncs.push(context.reconnect));
  return <p>page</p>;
}

describe('idle pause and resume', () => {
  let root;
  let container;
  let store;

  const sources = () => MockEventSource.instances;
  const current = () => sources().at(-1);
  const ready = (source, bootId = 'boot-1') => act(() => {
    source.emit('ui.event', envelope('connection.ready', { stream: 'ui', boot_id: bootId }));
  });
  const topicCalls = () => globalThis.fetch.mock.calls
    .filter(([url]) => url === `${STREAM_URL}/topics`)
    .map(([, init]) => JSON.parse(init.body))
    .map((body) => `${body.topic}:${body.watching}`);
  const text = () => container.textContent;

  // Time passes with a healthy stream: a heartbeat every 15 s keeps the stale-stream
  // watchdog and the silence dialog quiet, as the server's would.
  async function idleFor(ms) {
    for (let elapsed = 0; elapsed < ms; elapsed += 15000) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(Math.min(15000, ms - elapsed));
        if (!current().closed) current().emit('ui.event', envelope('connection.heartbeat'));
      });
    }
  }

  beforeEach(async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] });
    MockEventSource.instances = [];
    resyncs.length = 0;
    vi.stubGlobal('EventSource', MockEventSource);
    vi.stubGlobal('fetch', vi.fn(async (url, init) => ({
      status: 202,
      ok: true,
      text: async () => JSON.stringify({
        request_id: init.headers['X-Operation-Id'],
        event_types: ['stream.topic.updated'],
      }),
    })));
    resetStreamLifecycleForTest();
    resetStreamTopicsForTest();
    resetRequestRecoveryForTest();
    store = configureStore({
      reducer: { realtime: realtimeReducer, uiData: uiDataReducer },
      middleware: (getDefaultMiddleware) =>
        getDefaultMiddleware({ serializableCheck: false, immutableCheck: false })
          .prepend(realtimeListenerMiddleware.middleware),
    });
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        <Provider store={store}>
          <ConnectionRecoveryModal />
          <Page />
        </Provider>,
      );
    });
    act(() => {
      store.dispatch(startRealtimeStream({ streamUrl: STREAM_URL }));
    });
    await act(async () => {
      await Promise.resolve();
    });
    ready(current());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  });

  afterEach(async () => {
    store.dispatch(stopRealtimeStream());
    await act(async () => root.unmount());
    container.remove();
    vi.clearAllTimers();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('starts connected, with the page watching its topic and the dialog closed', () => {
    expect(sources()).toHaveLength(1);
    expect(store.getState().realtime.streamReady).toBe(true);
    expect(topicCalls()).toEqual(['agents:true']);
    expect(text()).not.toContain('Updates paused');
  });

  it('pauses after 30 minutes without input: the stream closes and the dialog stays until Resume is pressed', async () => {
    await idleFor(29 * MINUTE);
    expect(text()).not.toContain('Updates paused');
    expect(current().closed).toBe(false);

    await idleFor(2 * MINUTE);

    expect(text()).toContain('Updates paused');
    expect(text()).toContain('30 minutes');
    expect(sources()[0].closed).toBe(true);
    expect(store.getState().realtime.streamReady).toBe(false);
    expect(sources()).toHaveLength(1);

    // Input while paused does not dismiss it, and no stream is reopened behind it.
    act(() => {
      window.dispatchEvent(new Event('pointermove'));
      window.dispatchEvent(new Event('keydown'));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * MINUTE);
    });
    expect(text()).toContain('Updates paused');
    expect(sources()).toHaveLength(1);
  });

  it('input before the deadline keeps the session active', async () => {
    await idleFor(25 * MINUTE);
    act(() => {
      window.dispatchEvent(new Event('pointerdown'));
    });
    await idleFor(25 * MINUTE);

    expect(text()).not.toContain('Updates paused');
    expect(current().closed).toBe(false);
  });

  it('Resume opens a fresh stream and, once ready, registers the topic again and resyncs the page', async () => {
    await idleFor(31 * MINUTE);
    expect(text()).toContain('Updates paused');
    expect(topicCalls()).toEqual(['agents:true']);
    expect(resyncs).toEqual([false]);

    const resume = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Resume');
    await act(async () => {
      resume.click();
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(sources()).toHaveLength(2);
    expect(current().closed).toBe(false);
    expect(text()).not.toContain('Updates paused');

    // Topics and page state come back only when the server announces the new stream.
    expect(topicCalls()).toEqual(['agents:true']);
    ready(current());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(store.getState().realtime.streamReady).toBe(true);
    expect(topicCalls()).toEqual(['agents:true', 'agents:true']);
    expect(resyncs).toEqual([false, true]);
  });

  it('Resume works the same when the server restarted while paused', async () => {
    await idleFor(31 * MINUTE);
    const resume = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Resume');
    await act(async () => {
      resume.click();
    });
    await act(async () => {
      await Promise.resolve();
    });

    ready(current(), 'boot-2');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(store.getState().realtime.streamReady).toBe(true);
    expect(topicCalls()).toEqual(['agents:true', 'agents:true']);
    expect(resyncs).toEqual([false, true]);
    expect(text()).not.toContain('Updates paused');
  });

  it('after Resume the idle clock starts over, so the next pause is another 30 minutes away', async () => {
    await idleFor(31 * MINUTE);
    const resume = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Resume');
    await act(async () => {
      resume.click();
    });
    await act(async () => {
      await Promise.resolve();
    });
    ready(current());

    await idleFor(29 * MINUTE);
    expect(text()).not.toContain('Updates paused');
    await idleFor(2 * MINUTE);
    expect(text()).toContain('Updates paused');
  });
});
