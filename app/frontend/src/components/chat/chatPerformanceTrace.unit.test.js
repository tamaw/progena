// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const TRACE_STORAGE_KEY = 'progenitor:chat-performance-trace';

let originalUrl;
let originalHistoryState;

function createPerformanceObserver() {
  const observe = vi.fn();
  const disconnect = vi.fn();
  const PerformanceObserver = vi.fn(function PerformanceObserver(callback) {
    this.callback = callback;
    this.observe = observe;
    this.disconnect = disconnect;
  });

  return { PerformanceObserver, observe, disconnect };
}

async function loadTrace({ PerformanceObserver } = {}) {
  vi.resetModules();
  vi.stubGlobal('performance', {
    now: vi.fn(() => 100),
  });
  if (PerformanceObserver) {
    vi.stubGlobal('PerformanceObserver', PerformanceObserver);
  }

  return import('./chatPerformanceTrace');
}

async function initializeEnabledTrace() {
  const observer = createPerformanceObserver();
  window.history.replaceState(null, '', '/chat?chatPerformanceTrace=1');
  const trace = await loadTrace({
    PerformanceObserver: observer.PerformanceObserver,
  });

  trace.initializeChatPerformanceTrace();
  trace.clearChatPerformanceTrace();
  return { trace, observer };
}

beforeEach(() => {
  originalUrl = window.location.href;
  originalHistoryState = window.history.state;
  delete window.__progenitorChatPerf;
  window.localStorage.removeItem(TRACE_STORAGE_KEY);
  vi.spyOn(console, 'info').mockImplementation(() => {});
});

afterEach(() => {
  window.localStorage.removeItem(TRACE_STORAGE_KEY);
  delete window.__progenitorChatPerf;
  window.history.replaceState(originalHistoryState, '', originalUrl);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.resetModules();
});

describe('chatPerformanceTrace', () => {
  it('does not activate or emit entries without the exact visible query flag', async () => {
    window.localStorage.setItem(TRACE_STORAGE_KEY, '1');
    window.history.replaceState(null, '', '/chat?chatPerformanceTrace=true');
    const trace = await loadTrace();

    trace.initializeChatPerformanceTrace();

    expect(trace.isChatPerformanceTracingEnabled()).toBe(false);
    expect(trace.traceChatPerformance('stream.chunk_received', {
      streamId: 'stream-1',
      durationMs: 4,
    })).toBeNull();
    expect(trace.getChatPerformanceTrace()).toEqual([]);
    expect(console.info).not.toHaveBeenCalled();
    expect(trace).not.toHaveProperty('setChatPerformanceTracingEnabled');
    expect(window.__progenitorChatPerf).toBeUndefined();
  });

  it('initializes once from the visible query flag', async () => {
    const observer = createPerformanceObserver();
    window.history.replaceState(null, '', '/chat?chatPerformanceTrace=1');
    const trace = await loadTrace({
      PerformanceObserver: observer.PerformanceObserver,
    });

    expect(window.location.search).toBe('?chatPerformanceTrace=1');
    expect(trace.isChatPerformanceTracingEnabled()).toBe(false);

    trace.initializeChatPerformanceTrace();
    trace.initializeChatPerformanceTrace();

    expect(trace.isChatPerformanceTracingEnabled()).toBe(true);
    expect(observer.PerformanceObserver).toHaveBeenCalledTimes(1);
    expect(observer.observe).toHaveBeenCalledOnce();
    expect(observer.observe).toHaveBeenCalledWith({ type: 'longtask', buffered: true });
    expect(trace.getChatPerformanceTrace().filter(({ name }) => name === 'trace.enabled')).toHaveLength(1);
    expect(window.__progenitorChatPerf).toBeUndefined();
    expect(trace).not.toHaveProperty('setChatPerformanceTracingEnabled');
  });

  it('retains primitive timing metadata and excludes nested or raw payload details', async () => {
    const { trace } = await initializeEnabledTrace();

    const event = trace.traceChatPerformance('stream.chunk_received', {
      streamId: 'stream-1',
      requestId: 'request-1',
      runtimeStreamSeq: 7,
      durationMs: 12.34,
      queueDelayMs: 3,
      isTerminal: false,
      message: 'raw assistant response',
      prompt: 'raw user prompt',
      reasoning: 'raw model reasoning',
      toolArguments: { query: 'sensitive query' },
      toolResult: { content: 'sensitive result' },
      images: [{ base64: 'image bytes' }],
      nested: { payload: 'must not be captured' },
      list: ['must not be captured'],
    });

    expect(event).toEqual(expect.objectContaining({
      name: 'stream.chunk_received',
      streamId: 'stream-1',
      requestId: 'request-1',
      runtimeStreamSeq: 7,
      durationMs: 12.34,
      queueDelayMs: 3,
      isTerminal: false,
      atMs: expect.any(Number),
      wallTimeMs: expect.any(Number),
    }));
    expect(event).not.toHaveProperty('message');
    expect(event).not.toHaveProperty('prompt');
    expect(event).not.toHaveProperty('reasoning');
    expect(event).not.toHaveProperty('toolArguments');
    expect(event).not.toHaveProperty('toolResult');
    expect(event).not.toHaveProperty('images');
    expect(event).not.toHaveProperty('nested');
    expect(event).not.toHaveProperty('list');
  });

  it('keeps a bounded, oldest-first trace buffer', async () => {
    const { trace } = await initializeEnabledTrace();

    for (let sequence = 0; sequence <= 2_000; sequence += 1) {
      trace.traceChatPerformance('stream.chunk_received', { sequence });
    }

    const entries = trace.getChatPerformanceTrace();
    expect(entries).toHaveLength(2_000);
    expect(entries[0]).toMatchObject({ sequence: 1 });
    expect(entries.at(-1)).toMatchObject({ sequence: 2_000 });
  });

  it('clears every captured entry without changing initialized tracing', async () => {
    const { trace } = await initializeEnabledTrace();
    trace.traceChatPerformance('stream.chunk_received', { streamId: 'stream-1' });

    trace.clearChatPerformanceTrace();

    expect(trace.getChatPerformanceTrace()).toEqual([]);
    expect(trace.isChatPerformanceTracingEnabled()).toBe(true);
  });

  it('summarizes event counts by event name and stream or request', async () => {
    const { trace } = await initializeEnabledTrace();

    trace.traceChatPerformance('stream.chunk_received', {
      streamId: 'stream-1',
      durationMs: 2,
    });
    trace.traceChatPerformance('paint.committed', {
      streamId: 'stream-1',
      durationMs: 7,
    });
    trace.traceChatPerformance('request.started', {
      requestId: 'request-2',
    });

    expect(trace.summarizeChatPerformanceTrace()).toMatchObject({
      enabled: true,
      entryCount: 3,
      byName: {
        'stream.chunk_received': 1,
        'paint.committed': 1,
        'request.started': 1,
      },
      byStream: {
        'stream-1': {
          eventCount: 2,
          maxDurationMs: 7,
        },
        'request-2': {
          eventCount: 1,
          maxDurationMs: 0,
        },
      },
    });
  });
});
