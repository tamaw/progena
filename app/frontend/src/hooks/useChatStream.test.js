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
});
