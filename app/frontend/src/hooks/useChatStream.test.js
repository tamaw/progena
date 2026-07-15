import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react', () => ({
  useCallback: (callback) => callback,
  useEffect: () => undefined,
  useMemo: (factory) => factory(),
  useRef: (initialValue) => ({ current: initialValue }),
  useState: (initialValue) => [typeof initialValue === 'function' ? initialValue() : initialValue, vi.fn()],
}));

vi.mock('react-redux', () => ({
  useSelector: () => [],
}));

import { useChatStream } from './useChatStream';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('useChatStream', () => {
  it('publishes the first accepted chunk to the live turn before animation frames run', () => {
    const scheduledFrames = [];
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback) => {
      scheduledFrames.push(callback);
      return scheduledFrames.length;
    }));

    let onChunk;
    const sendMessageStream = vi.fn((content, history, nextOnChunk) => {
      onChunk = nextOnChunk;
      return new Promise(() => {});
    });
    const hook = useChatStream({
      messages: [],
      setMessages: vi.fn(),
      activeBackendChat: null,
      selectedBackendChatId: null,
      threadId: null,
      sendMessageStream,
      cancelStream: vi.fn(),
      dispatch: vi.fn(),
      getApiUrl: vi.fn(),
      show: vi.fn(),
      selectedBackendChatIdRef: { current: null },
      reloadOpenChat: vi.fn(),
    });
    void hook.handleSend('Hello');
    expect(sendMessageStream).toHaveBeenCalledOnce();

    const publishedTurns = [];
    hook.liveTurnStore.subscribe(() => publishedTurns.push(hook.liveTurnStore.getSnapshot()));

    onChunk('First visible chunk');

    expect(scheduledFrames).toEqual([]);
    expect(publishedTurns).toHaveLength(1);
    expect(publishedTurns[0]).toMatchObject({
      content: 'First visible chunk',
      assistantMessageId: expect.any(String),
    });
  });

  it('flushes queued text before committing the terminal response', () => {
    const scheduledFrames = [];
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback) => {
      scheduledFrames.push(callback);
      return scheduledFrames.length;
    }));

    let onChunk;
    let onComplete;
    const sendMessageStream = vi.fn((content, history, nextOnChunk, nextOnComplete) => {
      onChunk = nextOnChunk;
      onComplete = nextOnComplete;
      return new Promise(() => {});
    });
    const setMessages = vi.fn();
    const hook = useChatStream({
      messages: [],
      setMessages,
      activeBackendChat: null,
      selectedBackendChatId: null,
      threadId: null,
      sendMessageStream,
      cancelStream: vi.fn(),
      dispatch: vi.fn(),
      getApiUrl: vi.fn(),
      show: vi.fn(),
      selectedBackendChatIdRef: { current: null },
      reloadOpenChat: vi.fn(),
    });
    void hook.handleSend('Hello');

    onChunk('First');
    onChunk(' second');
    onChunk(' third');

    expect(hook.liveTurnStore.getSnapshot().content).toBe('First');
    expect(scheduledFrames).toHaveLength(1);

    onComplete('completed');

    expect(hook.liveTurnStore.getSnapshot()).toBeNull();
    const committedMessages = setMessages.mock.calls.reduce(
      (messages, [update]) => update(messages),
      [],
    );
    expect(committedMessages).toEqual(expect.arrayContaining([
      expect.objectContaining({ role: 'assistant', content: 'First second third' }),
    ]));
  });

  it('flushes queued text before publishing a structural stream event', () => {
    const scheduledFrames = [];
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback) => {
      scheduledFrames.push(callback);
      return scheduledFrames.length;
    }));

    let onChunk;
    let onStreamEvent;
    const hook = useChatStream({
      messages: [],
      setMessages: vi.fn(),
      activeBackendChat: null,
      selectedBackendChatId: null,
      threadId: null,
      sendMessageStream: vi.fn((content, history, nextOnChunk, onComplete, onError, nextOnStreamEvent) => {
        onChunk = nextOnChunk;
        onStreamEvent = nextOnStreamEvent;
        return new Promise(() => {});
      }),
      cancelStream: vi.fn(),
      dispatch: vi.fn(),
      getApiUrl: vi.fn(),
      show: vi.fn(),
      selectedBackendChatIdRef: { current: null },
      reloadOpenChat: vi.fn(),
    });
    void hook.handleSend('Hello');

    onChunk('First');
    onChunk(' second');
    expect(scheduledFrames).toHaveLength(1);

    onStreamEvent({ event_type: 'node_update', content: 'Planning' });

    expect(hook.liveTurnStore.getSnapshot()).toMatchObject({
      content: 'First second',
      streamEvents: [expect.objectContaining({ eventType: 'node_update', content: 'Planning' })],
    });
  });

  it('does not apply a superseded stream frame to the replacement live turn', () => {
    const scheduledFrames = [];
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback) => {
      scheduledFrames.push(callback);
      return scheduledFrames.length;
    }));
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
    let now = 0;
    vi.spyOn(Date, 'now').mockImplementation(() => ++now);

    const chunkHandlers = [];
    const sendMessageStream = vi.fn((content, history, onChunk) => {
      chunkHandlers.push(onChunk);
      return new Promise(() => {});
    });
    const hook = useChatStream({
      messages: [],
      setMessages: vi.fn(),
      activeBackendChat: null,
      selectedBackendChatId: null,
      threadId: null,
      sendMessageStream,
      cancelStream: vi.fn(),
      dispatch: vi.fn(),
      getApiUrl: vi.fn(),
      show: vi.fn(),
      selectedBackendChatIdRef: { current: null },
      reloadOpenChat: vi.fn(),
    });

    void hook.handleSend('First request');
    chunkHandlers[0]('A first');
    chunkHandlers[0](' A queued');
    expect(scheduledFrames).toHaveLength(1);

    void hook.handleSend('Second request');
    chunkHandlers[1]('B first');
    chunkHandlers[1](' B queued');
    expect(scheduledFrames).toHaveLength(2);

    // Simulate A's already-cancelled frame running after B queued its own delta.
    scheduledFrames[0]();
    scheduledFrames[1]();

    expect(hook.liveTurnStore.getSnapshot()).toMatchObject({
      content: 'B first B queued',
    });
  });

  it('releases foreground ownership before reloading a completed thread', () => {
    let onComplete;
    const setMessages = vi.fn();
    const authoritativeMessages = [{
      id: 'persisted-assistant',
      role: 'assistant',
      content: 'Persisted authoritative response',
    }];
    let hook;
    const reloadOpenChat = vi.fn((threadId) => {
      // Detail hydration must observe no foreground/background stream owner.
      expect(threadId).toBe('thread-a');
      expect(hook.activeStreamIdRef.current).toBeNull();
      expect(hook.backgroundOriginChatIdRef.current).toBeNull();
      setMessages(() => authoritativeMessages);
    });

    hook = useChatStream({
      messages: [],
      setMessages,
      activeBackendChat: { thread_id: 'thread-a' },
      selectedBackendChatId: 'thread-a',
      threadId: 'thread-a',
      sendMessageStream: vi.fn((content, history, onChunk, nextOnComplete) => {
        onComplete = nextOnComplete;
        return new Promise(() => {});
      }),
      cancelStream: vi.fn(),
      dispatch: vi.fn(),
      getApiUrl: vi.fn(),
      show: vi.fn(),
      selectedBackendChatIdRef: { current: 'thread-a' },
      reloadOpenChat,
    });

    void hook.handleSend('Hello');
    onComplete('completed');

    expect(reloadOpenChat).toHaveBeenCalledOnce();
    const hydrate = setMessages.mock.calls.at(-1)[0];
    expect(hydrate([])).toEqual(authoritativeMessages);
  });
});
