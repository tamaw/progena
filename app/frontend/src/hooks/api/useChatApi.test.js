import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { publishUiEvent } from '../../store/realtimeEvents';

const mocks = vi.hoisted(() => ({
  effectCleanups: [],
  getApiUrl: vi.fn(),
}));

vi.mock('react', () => ({
  useCallback: (callback) => callback,
  useEffect: (effect) => {
    const cleanup = effect();
    if (typeof cleanup === 'function') {
      mocks.effectCleanups.push(cleanup);
    }
  },
  useRef: (initialValue) => ({ current: initialValue }),
  useState: (initialValue) => [
    typeof initialValue === 'function' ? initialValue() : initialValue,
    vi.fn(),
  ],
}));

vi.mock('../../context/ConfigContext', () => ({
  useConfig: () => ({
    config: { chat: { defaultModel: 'test-model' } },
    getApiUrl: mocks.getApiUrl,
  }),
}));

import { useChatApi } from './useChatApi';

const RESPONSES_URL = '/api/ui/responses';
const REQUEST_ID = '11111111-1111-4111-8111-111111111111';
const CANCEL_REQUEST_ID = '22222222-2222-4222-8222-222222222222';
const FRONTEND_RESPONSE_OPTIONS_KEY = '__frontendResponse';

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

  emit(type, payload, lastEventId = '') {
    for (const listener of this.listeners.get(type) || []) {
      listener({ data: JSON.stringify(payload), lastEventId });
    }
  }
}

function acceptedResponse(requestId, eventTypes) {
  return {
    status: 202,
    text: vi.fn().mockResolvedValue(JSON.stringify({
      request_id: requestId,
      event_types: eventTypes,
    })),
  };
}

async function flushMicrotasks() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

function publishLifecycle(type, requestId = REQUEST_ID, payload = {}) {
  publishUiEvent({
    type,
    payload: { request_id: requestId, ...payload },
  });
}

describe('useChatApi dedicated response transport', () => {
  let fetchMock;

  beforeEach(() => {
    MockEventSource.instances = [];
    mocks.effectCleanups = [];
    mocks.getApiUrl.mockReset();
    mocks.getApiUrl.mockReturnValue(RESPONSES_URL);

    vi.stubGlobal('EventSource', MockEventSource);
    vi.stubGlobal('crypto', {
      randomUUID: vi.fn(() => REQUEST_ID),
    });
    fetchMock = vi.fn((url, options) => Promise.resolve(acceptedResponse(
      options.headers['X-Request-ID'],
      options.method === 'PUT' ? ['chat.response.cancel_requested'] : ['chat.response.started'],
    )));
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    for (const cleanup of mocks.effectCleanups.splice(0)) {
      cleanup();
    }
    vi.clearAllTimers();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('opens the acknowledged request stream, forwards ordered content frames, and closes only at canonical completion', async () => {
    const observed = [];
    const onComplete = vi.fn((result) => observed.push(['complete', result]));
    const onError = vi.fn();
    const hook = useChatApi();

    const terminal = hook.sendMessageStream(
      'What changed?',
      [{ role: 'assistant', content: 'Earlier reply' }],
      (delta) => observed.push(['delta', delta]),
      onComplete,
      onError,
      (event) => observed.push(['event', event.event_type, event.content]),
    );
    await flushMicrotasks();

    expect(fetchMock).toHaveBeenCalledWith(RESPONSES_URL, expect.objectContaining({ method: 'POST' }));
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({
      stream: true,
      input: [
        { role: 'assistant', content: 'Earlier reply' },
        { role: 'user', content: 'What changed?' },
      ],
    });
    expect(MockEventSource.instances).toHaveLength(1);
    const source = MockEventSource.instances[0];
    expect(source.url).toBe(`${RESPONSES_URL}/${REQUEST_ID}/stream`);

    source.emit('chat.response.delta', { delta: 'First ' }, `chat-response:${REQUEST_ID}:1`);
    source.emit('chat.response.event', {
      event_type: 'tool_call',
      content: 'Searching',
      metadata: { tool_name: 'search' },
    }, `chat-response:${REQUEST_ID}:2`);
    source.emit('chat.response.delta', { delta: 'answer' }, `chat-response:${REQUEST_ID}:3`);

    expect(observed).toEqual([
      ['delta', 'First '],
      ['event', 'tool_call', 'Searching'],
      ['delta', 'answer'],
    ]);

    publishLifecycle('chat.response.started');
    expect(source.closed).toBe(false);

    publishLifecycle('chat.response.completed');
    await terminal;

    expect(source.closed).toBe(true);
    expect(onComplete).toHaveBeenCalledOnce();
    expect(onComplete).toHaveBeenCalledWith('');
    expect(onError).not.toHaveBeenCalled();
    expect(observed).toEqual([
      ['delta', 'First '],
      ['event', 'tool_call', 'Searching'],
      ['delta', 'answer'],
      ['complete', ''],
    ]);
  });

  it('reconnects after a transport error with the last dedicated frame cursor', async () => {
    vi.useFakeTimers();
    const hook = useChatApi();
    const terminal = hook.sendMessageStream('Resume me', [], vi.fn(), vi.fn(), vi.fn(), vi.fn());
    await flushMicrotasks();

    const first = MockEventSource.instances[0];
    const cursor = `chat-response:${REQUEST_ID}:7`;
    first.emit('chat.response.delta', { delta: 'already rendered' }, cursor);
    first.onerror();

    expect(first.closed).toBe(true);
    expect(MockEventSource.instances).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(1000);

    expect(MockEventSource.instances).toHaveLength(2);
    expect(MockEventSource.instances[1].url).toBe(
      `${RESPONSES_URL}/${REQUEST_ID}/stream?after=${encodeURIComponent(cursor)}`,
    );

    publishLifecycle('chat.response.completed');
    await vi.advanceTimersByTimeAsync(50);
    await terminal;
  });

  it('waits through the terminal drain window so late dedicated frames still reach the caller', async () => {
    vi.useFakeTimers();
    const observed = [];
    const onComplete = vi.fn((result) => observed.push(['complete', result]));
    const hook = useChatApi();
    const terminal = hook.sendMessageStream(
      'Finish after the last frame',
      [],
      (delta) => observed.push(['delta', delta]),
      onComplete,
      vi.fn(),
      (event) => observed.push(['event', event.event_type, event.content]),
    );
    await flushMicrotasks();

    const source = MockEventSource.instances[0];
    source.emit('chat.response.delta', { delta: 'First' }, `chat-response:${REQUEST_ID}:1`);

    publishLifecycle('chat.response.completed');
    expect(onComplete).not.toHaveBeenCalled();

    source.emit('chat.response.delta', { delta: ' late' }, `chat-response:${REQUEST_ID}:2`);
    source.emit('chat.response.event', {
      event_type: 'tool_result',
      content: 'arrived after terminal',
      metadata: {},
    }, `chat-response:${REQUEST_ID}:3`);

    await vi.advanceTimersByTimeAsync(49);
    expect(onComplete).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1);
    await terminal;

    expect(observed).toEqual([
      ['delta', 'First'],
      ['delta', ' late'],
      ['event', 'tool_result', 'arrived after terminal'],
      ['complete', ''],
    ]);
  });

  it('allows opted-in chat streams to wait for the canonical terminal lifecycle after resynchronization', async () => {
    vi.useFakeTimers();
    const onComplete = vi.fn();
    const onError = vi.fn();
    const hook = useChatApi();
    const terminal = hook.sendMessageStream(
      'Recover persisted reply',
      [],
      vi.fn(),
      onComplete,
      onError,
      vi.fn(),
      { [FRONTEND_RESPONSE_OPTIONS_KEY]: { allowDesyncedCompletion: true } },
    );
    let settled = false;
    terminal.then(() => {
      settled = true;
    });
    await flushMicrotasks();

    const source = MockEventSource.instances[0];
    source.emit('chat.response.resync_required', { request_id: REQUEST_ID, reason: 'cursor_expired' });
    await flushMicrotasks();

    expect(source.closed).toBe(true);
    expect(settled).toBe(false);
    expect(onComplete).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();

    publishLifecycle('chat.response.completed');
    await vi.runOnlyPendingTimersAsync();
    await terminal;

    expect(settled).toBe(true);
    expect(onComplete).toHaveBeenCalledOnce();
    expect(onComplete).toHaveBeenCalledWith('');
    expect(onError).not.toHaveBeenCalled();
  });

  it('fails desynced quick-chat completions explicitly instead of silently completing empty', async () => {
    vi.useFakeTimers();
    const onComplete = vi.fn();
    const onError = vi.fn();
    const hook = useChatApi();
    const terminal = hook.sendMessageStream('Recover persisted reply', [], vi.fn(), onComplete, onError, vi.fn());
    await flushMicrotasks();

    const source = MockEventSource.instances[0];
    source.emit('chat.response.resync_required', { request_id: REQUEST_ID, reason: 'cursor_expired' });
    publishLifecycle('chat.response.completed');

    await vi.runOnlyPendingTimersAsync();
    await terminal;

    expect(onComplete).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledOnce();
    expect(onError.mock.calls[0][0]).toEqual(expect.objectContaining({
      message: 'Response stream expired before completion',
    }));
  });

  it('ignores cancel_requested until the target request receives canonical cancellation', async () => {
    vi.useFakeTimers();
    const onComplete = vi.fn();
    const hook = useChatApi();
    const terminal = hook.sendMessageStream('Stop this response', [], vi.fn(), onComplete, vi.fn(), vi.fn());
    let settled = false;
    terminal.then(() => {
      settled = true;
    });
    await flushMicrotasks();

    const source = MockEventSource.instances[0];
    globalThis.crypto.randomUUID.mockReturnValueOnce(CANCEL_REQUEST_ID);
    const cancelPromise = hook.cancelStream();
    await flushMicrotasks();

    expect(fetchMock).toHaveBeenLastCalledWith(
      `${RESPONSES_URL}/${REQUEST_ID}/cancel`,
      expect.objectContaining({ method: 'PUT' }),
    );

    publishUiEvent({
      type: 'chat.response.cancel_requested',
      payload: {
        target_request_id: REQUEST_ID,
        cancel_request_id: CANCEL_REQUEST_ID,
        cancelled: true,
      },
    });
    await flushMicrotasks();

    expect(settled).toBe(false);
    expect(source.closed).toBe(false);
    expect(onComplete).not.toHaveBeenCalled();

    publishLifecycle('chat.response.cancelled');
    await vi.runOnlyPendingTimersAsync();
    await cancelPromise;
    await terminal;

    expect(source.closed).toBe(true);
    expect(onComplete).toHaveBeenCalledOnce();
    expect(onComplete).toHaveBeenCalledWith('cancelled');
  });

  it('allows a reopened panel to explicitly cancel an observed active stream', async () => {
    const hook = useChatApi();
    const cleanup = hook.observeResponseStream(REQUEST_ID);
    await flushMicrotasks();

    expect(MockEventSource.instances).toHaveLength(1);
    globalThis.crypto.randomUUID.mockReturnValueOnce(CANCEL_REQUEST_ID);
    await hook.cancelStream();

    expect(fetchMock).toHaveBeenLastCalledWith(
      `${RESPONSES_URL}/${REQUEST_ID}/cancel`,
      expect.objectContaining({ method: 'PUT' }),
    );

    cleanup();
    expect(MockEventSource.instances[0].closed).toBe(true);
  });
});
