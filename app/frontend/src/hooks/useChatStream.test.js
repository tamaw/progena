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
vi.mock('../components/chat/liveAssistantTurnModel', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    applyAssistantChunk: vi.fn(actual.applyAssistantChunk),
  };
});


import { useChatStream } from './useChatStream';
import { markChatGenerating } from '../store/uiDataSlice';
import { applyAssistantChunk } from '../components/chat/liveAssistantTurnModel';


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
      onForegroundStreamStarted: vi.fn(),
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

  it('aggregates delayed text deltas into one model update before terminal flush', () => {
    const scheduledFrames = [];
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback) => {
      scheduledFrames.push(callback);
      return scheduledFrames.length;
    }));
    vi.stubGlobal('cancelAnimationFrame', vi.fn());

    let onChunk;
    let onComplete;
    const setMessages = vi.fn();
    const hook = useChatStream({
      messages: [],
      setMessages,
      activeBackendChat: null,
      selectedBackendChatId: null,
      threadId: null,
      sendMessageStream: vi.fn((content, history, nextOnChunk, nextOnComplete) => {
        onChunk = nextOnChunk;
        onComplete = nextOnComplete;
        return new Promise(() => {});
      }),
      cancelStream: vi.fn(),
      dispatch: vi.fn(),
      getApiUrl: vi.fn(),
      show: vi.fn(),
      selectedBackendChatIdRef: { current: null },
      reloadOpenChat: vi.fn(),
      onForegroundStreamStarted: vi.fn(),
      requestForegroundReconciliation: vi.fn(),
    });
    void hook.handleSend('Hello');

    onChunk('First');
    applyAssistantChunk.mockClear();

    onChunk(' second');
    onChunk(' third');
    onChunk(' fourth');

    expect(scheduledFrames).toHaveLength(1);
    expect(applyAssistantChunk).not.toHaveBeenCalled();

    scheduledFrames[0]();

    expect(applyAssistantChunk).toHaveBeenCalledOnce();
    expect(applyAssistantChunk).toHaveBeenCalledWith(
      expect.objectContaining({ content: 'First' }),
      ' second third fourth',
      expect.any(Number),
    );
    expect(hook.liveTurnStore.getSnapshot()).toMatchObject({
      content: 'First second third fourth',
    });

    onChunk(' final');
    expect(scheduledFrames).toHaveLength(2);
    applyAssistantChunk.mockClear();

    // Completion is a terminal boundary: it must synchronously flush the
    // final pending text instead of waiting for its captured rAF.
    onComplete('completed');

    expect(applyAssistantChunk).toHaveBeenCalledOnce();
    expect(applyAssistantChunk).toHaveBeenCalledWith(
      expect.objectContaining({ content: 'First second third fourth' }),
      ' final',
      expect.any(Number),
    );
    expect(hook.liveTurnStore.getSnapshot()).toMatchObject({
      content: 'First second third fourth final',
    });

    // The first terminal frame lets the external-store update paint; only the
    // second may commit local history and finalize the live turn in place.
    expect(scheduledFrames).toHaveLength(3);
    scheduledFrames[2]();
    expect(hook.liveTurnStore.getSnapshot()).toMatchObject({
      content: 'First second third fourth final',
    });
    expect(scheduledFrames).toHaveLength(4);
    scheduledFrames[3]();
    expect(hook.liveTurnStore.getSnapshot()).toMatchObject({
      content: 'First second third fourth final',
      isStreaming: false,
    });

    // A frame captured before completion cannot replay a terminally flushed update.
    scheduledFrames[1]();
    expect(applyAssistantChunk).toHaveBeenCalledOnce();
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
      onForegroundStreamStarted: vi.fn(),
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
      onForegroundStreamStarted: vi.fn(),
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

  it('releases the foreground turn after preserving its stream in the background', () => {
    const dispatch = vi.fn();
    const cancelStream = vi.fn();
    let onChunk;
    const hook = useChatStream({
      messages: [],
      setMessages: vi.fn(),
      activeBackendChat: { thread_id: 'thread-a' },
      selectedBackendChatId: 'thread-a',
      threadId: 'thread-a',
      sendMessageStream: vi.fn((content, history, nextOnChunk) => {
        onChunk = nextOnChunk;
        return new Promise(() => {});
      }),
      cancelStream,
      dispatch,
      getApiUrl: vi.fn(),
      show: vi.fn(),
      selectedBackendChatIdRef: { current: 'thread-a' },
      reloadOpenChat: vi.fn(),
      onForegroundStreamStarted: vi.fn(),
    });

    void hook.handleSend('Keep running');
    onChunk('Partial reply');
    hook.preserveStreamInBackground();
    hook.retireForegroundStream();

    expect(dispatch).toHaveBeenCalledWith(markChatGenerating('thread-a'));
    expect(hook.activeStreamIdRef.current).toBeNull();
    expect(hook.liveTurnStore.getSnapshot()).toBeNull();
    expect(cancelStream).not.toHaveBeenCalled();
  });

  it('marks the superseded origin chat as generating when a replacement stream starts', () => {
    const dispatch = vi.fn();
    const hook = useChatStream({
      messages: [],
      setMessages: vi.fn(),
      activeBackendChat: { thread_id: 'thread-a' },
      selectedBackendChatId: 'thread-a',
      threadId: 'thread-a',
      sendMessageStream: vi.fn(() => new Promise(() => {})),
      cancelStream: vi.fn(),
      dispatch,
      getApiUrl: vi.fn(),
      show: vi.fn(),
      selectedBackendChatIdRef: { current: 'thread-a' },
      reloadOpenChat: vi.fn(),
      onForegroundStreamStarted: vi.fn(),
    });

    void hook.handleSend('First request');
    dispatch.mockClear();

    void hook.handleSend('Replacement request');

    expect(dispatch).toHaveBeenCalledWith(markChatGenerating('thread-a'));
  });

  it('keeps a foreground completed turn visible through a paint before committing it locally', () => {
    const scheduledFrames = [];
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback) => {
      scheduledFrames.push(callback);
      return scheduledFrames.length;
    }));
    vi.stubGlobal('cancelAnimationFrame', vi.fn());

    let onChunk;
    let onComplete;
    let messagesCommittedBySetter = [];
    const setMessages = vi.fn((update) => {
      messagesCommittedBySetter = update(messagesCommittedBySetter);
    });
    const reloadOpenChat = vi.fn();
    let messagesAtReconciliation = null;
    const requestForegroundReconciliation = vi.fn(() => {
      messagesAtReconciliation = messagesCommittedBySetter;
    });
    const hook = useChatStream({
      messages: [],
      setMessages,
      activeBackendChat: { thread_id: 'thread-a' },
      selectedBackendChatId: 'thread-a',
      threadId: 'thread-a',
      sendMessageStream: vi.fn((content, history, nextOnChunk, nextOnComplete) => {
        onChunk = nextOnChunk;
        onComplete = nextOnComplete;
        return new Promise(() => {});
      }),
      cancelStream: vi.fn(),
      dispatch: vi.fn(),
      getApiUrl: vi.fn(),
      show: vi.fn(),
      selectedBackendChatIdRef: { current: 'thread-a' },
      reloadOpenChat,
      onForegroundStreamStarted: vi.fn(),
      requestForegroundReconciliation,
    });

    void hook.handleSend('Hello');
    onChunk('First visible');
    onChunk(' terminal text');
    const streamingTurn = hook.liveTurnStore.getSnapshot();
    expect(streamingTurn).toMatchObject({
      streamId: expect.any(String),
      assistantMessageId: expect.any(String),
      isStreaming: true,
    });
    onComplete('completed');

    // Terminal text is synchronously visible, but must survive two animation
    // frames: the first gives the external store a paint opportunity; the
    // second commits local history and finalizes that same live turn in place.
    expect(hook.liveTurnStore.getSnapshot()).toMatchObject({
      content: 'First visible terminal text',
    });
    expect(reloadOpenChat).not.toHaveBeenCalled();
    expect(requestForegroundReconciliation).not.toHaveBeenCalled();
    const messagesBeforePaint = setMessages.mock.calls.reduce(
      (messages, [update]) => update(messages),
      [],
    );
    expect(messagesBeforePaint).toEqual([
      expect.objectContaining({ role: 'user', content: 'Hello' }),
    ]);

    expect(scheduledFrames).toHaveLength(2);
    scheduledFrames[1]();

    expect(hook.liveTurnStore.getSnapshot()).toMatchObject({
      content: 'First visible terminal text',
    });
    expect(scheduledFrames).toHaveLength(3);
    expect(reloadOpenChat).not.toHaveBeenCalled();
    expect(requestForegroundReconciliation).not.toHaveBeenCalled();
    const messagesAfterFirstPaint = setMessages.mock.calls.reduce(
      (messages, [update]) => update(messages),
      [],
    );
    expect(messagesAfterFirstPaint).toEqual([
      expect.objectContaining({ role: 'user', content: 'Hello' }),
    ]);

    scheduledFrames[2]();

    const completedLiveTurn = hook.liveTurnStore.getSnapshot();
    expect(completedLiveTurn).toMatchObject({
      content: 'First visible terminal text',
      isStreaming: false,
      streamId: streamingTurn.streamId,
      assistantMessageId: streamingTurn.assistantMessageId,
    });
    const messagesAfterSecondPaint = setMessages.mock.calls.reduce(
      (messages, [update]) => update(messages),
      [],
    );
    expect(messagesAfterSecondPaint).toEqual([
      expect.objectContaining({ role: 'user', content: 'Hello' }),
      expect.objectContaining({ role: 'assistant', content: 'First visible terminal text' }),
    ]);
    expect(messagesAfterSecondPaint.filter(
      ({ role, content }) => role === 'assistant' && content === 'First visible terminal text',
    )).toHaveLength(1);
    expect(reloadOpenChat).not.toHaveBeenCalled();
    expect(requestForegroundReconciliation).toHaveBeenCalledOnce();
    expect(requestForegroundReconciliation).toHaveBeenCalledWith({
      threadId: 'thread-a',
      streamId: expect.any(String),
    });
    expect(messagesAtReconciliation).toEqual([
      expect.objectContaining({ role: 'user', content: 'Hello' }),
      expect.objectContaining({ role: 'assistant', content: 'First visible terminal text' }),
    ]);
  });

  it('commits response frames accepted between terminal handoff animation frames', () => {
    const scheduledFrames = [];
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback) => {
      scheduledFrames.push(callback);
      return scheduledFrames.length;
    }));
    vi.stubGlobal('cancelAnimationFrame', vi.fn());

    let onChunk;
    let onComplete;
    let onStreamEvent;
    let messagesCommittedBySetter = [];
    const setMessages = vi.fn((update) => {
      messagesCommittedBySetter = update(messagesCommittedBySetter);
    });
    const hook = useChatStream({
      messages: [],
      setMessages,
      activeBackendChat: { thread_id: 'thread-a' },
      selectedBackendChatId: 'thread-a',
      threadId: 'thread-a',
      sendMessageStream: vi.fn((content, history, nextOnChunk, nextOnComplete, onError, nextOnStreamEvent) => {
        onChunk = nextOnChunk;
        onComplete = nextOnComplete;
        onStreamEvent = nextOnStreamEvent;
        return new Promise(() => {});
      }),
      cancelStream: vi.fn(),
      dispatch: vi.fn(),
      getApiUrl: vi.fn(),
      show: vi.fn(),
      selectedBackendChatIdRef: { current: 'thread-a' },
      reloadOpenChat: vi.fn(),
      onForegroundStreamStarted: vi.fn(),
      requestForegroundReconciliation: vi.fn(),
    });

    void hook.handleSend('Hello');
    onChunk('Initial output');
    onComplete('completed');

    // The first handoff frame has painted the terminal snapshot and queued its
    // second frame. Content frames may still arrive before that second frame.
    expect(scheduledFrames).toHaveLength(1);
    scheduledFrames[0]();
    expect(scheduledFrames).toHaveLength(2);

    onChunk(' late chunk');
    onStreamEvent({ event_type: 'node_update', content: 'Late structured content' });

    expect(hook.liveTurnStore.getSnapshot()).toMatchObject({
      content: 'Initial output late chunk',
      streamEvents: [expect.objectContaining({
        eventType: 'node_update',
        content: 'Late structured content',
      })],
    });

    scheduledFrames[1]();

    expect(hook.liveTurnStore.getSnapshot()).toMatchObject({
      content: 'Initial output late chunk',
      isStreaming: false,
      streamEvents: [expect.objectContaining({
        eventType: 'node_update',
        content: 'Late structured content',
      })],
    });
    expect(messagesCommittedBySetter).toEqual([
      expect.objectContaining({ role: 'user', content: 'Hello' }),
      expect.objectContaining({
        role: 'assistant',
        content: 'Initial output late chunk',
        streamEvents: [expect.objectContaining({
          eventType: 'node_update',
          content: 'Late structured content',
        })],
      }),
    ]);
  });
});
