// @vitest-environment jsdom
import React, { act, createRef, forwardRef, useImperativeHandle, useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRoot } from 'react-dom/client';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const mocks = vi.hoisted(() => ({
  listener: null,
  observers: new Map(),
  observeResponseStream: vi.fn(),
  reportFailure: vi.fn(),
  cancelStream: vi.fn(),
  dispatch: vi.fn(),
  show: vi.fn(),
  requestForegroundReconciliation: vi.fn(),
  sendMessageStream: vi.fn(),
  queueMessage: vi.fn(),
  getApiUrl: (path) => path,
  reloadOpenChat: vi.fn(),
  onForegroundStreamStarted: vi.fn(),
}));

vi.mock('react-redux', () => ({
  useSelector: () => [],
}));
vi.mock('@frontend/lib/failures', () => ({
  reportFailure: mocks.reportFailure,
}));
vi.mock('@frontend/realtime/realtimeEvents', () => ({
  subscribeUiEvent: (listener) => {
    mocks.listener = listener;
    return () => {
      if (mocks.listener === listener) mocks.listener = null;
    };
  },
}));

import { useChatStream } from '@frontend/features/chat/hooks/useChatStream';
import { UI_EVENT } from '@frontend/realtime/eventVocabulary';

const Harness = forwardRef(function Harness({ threadId = 'thread-1' }, ref) {
  const [messages, setMessages] = useState([]);
  const stream = useChatStream({
    messages,
    setMessages,
    activeBackendChat: { thread_id: threadId },
    selectedBackendChatId: threadId,
    threadId,
    sendMessageStream: mocks.sendMessageStream,
    queueMessage: mocks.queueMessage,
    cancelStream: mocks.cancelStream,
    observeResponseStream: mocks.observeResponseStream,
    dispatch: mocks.dispatch,
    getApiUrl: mocks.getApiUrl,
    show: mocks.show,
    selectedBackendChatIdRef: { current: threadId },
    reloadOpenChat: mocks.reloadOpenChat,
    onForegroundStreamStarted: mocks.onForegroundStreamStarted,
    requestForegroundReconciliation: mocks.requestForegroundReconciliation,
  });
  useImperativeHandle(ref, () => ({ ...stream, messages }), [messages, stream]);
  return null;
});

function started(requestId, queuedMessageId) {
  return {
    type: UI_EVENT.CHAT_RESPONSE_STARTED,
    payload: {
      thread_id: 'thread-1',
      request_id: `queue-${requestId}`,
      content_request_id: requestId,
      queue_execution: true,
      queued_message: { id: queuedMessageId, text: `human ${requestId}`, attachments: [] },
    },
  };
}

async function publish(event) {
  await act(async () => {
    mocks.listener(event);
    await Promise.resolve();
  });
}

let host;
let root;
let streamRef;
let animationFrames;

async function mount() {
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  streamRef = createRef();
  await act(async () => {
    root.render(<Harness ref={streamRef} />);
    await Promise.resolve();
  });
}

beforeEach(async () => {
  mocks.listener = null;
  mocks.observers = new Map();
  mocks.observeResponseStream.mockReset();
  mocks.sendMessageStream.mockReset();
  mocks.queueMessage.mockReset();
  mocks.reloadOpenChat.mockReset();
  mocks.onForegroundStreamStarted.mockReset();
  animationFrames = [];
  vi.stubGlobal('requestAnimationFrame', vi.fn((callback) => {
    animationFrames.push(callback);
    return animationFrames.length;
  }));
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
  mocks.observeResponseStream.mockImplementation((requestId, callbacks) => {
    const cleanup = vi.fn();
    mocks.observers.set(requestId, { ...callbacks, cleanup });
    return cleanup;
  });
  await mount();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('useChatStream queued execution ownership', () => {
  it('keeps late B text and tool frames in B after C becomes the live turn', async () => {
    await publish(started('B', 'human-b'));
    await act(async () => {
      mocks.observers.get('B').onChunk('B initial');
    });

    await publish(started('C', 'human-c'));
    await act(async () => {
      mocks.observers.get('C').onChunk('C output');
      mocks.observers.get('B').onChunk(' B late');
      mocks.observers.get('B').onStreamEvent({
        event_type: 'tool_start',
        content: 'Late B tool',
        metadata: { tool_call_id: 'tool-b', tool_name: 'lookup' },
      });
    });

    expect(streamRef.current.liveTurnStore.getSnapshot()).toMatchObject({
      streamId: 'queued_C',
      content: 'C output',
    });
    expect(streamRef.current.messages).toEqual([
      expect.objectContaining({ id: 'human-b', role: 'user' }),
      expect.objectContaining({
        id: 'B-assistant',
        role: 'assistant',
        streamEvents: [expect.objectContaining({
          eventType: 'tool_start',
          content: 'Late B tool',
          metadata: expect.objectContaining({ commentary: 'B initial B late' }),
        })],
      }),
      expect.objectContaining({ id: 'human-c', role: 'user' }),
    ]);
  });

  it('hands a completed root turn to B once without closing B during the handoff', async () => {
    let rootOnChunk;
    let rootOnComplete;
    mocks.sendMessageStream.mockImplementation((content, history, onChunk, onComplete) => {
      rootOnChunk = onChunk;
      rootOnComplete = onComplete;
      return new Promise(() => {});
    });

    await act(async () => {
      void streamRef.current.handleSend('root A');
    });
    await act(async () => {
      rootOnChunk('A reply');
      rootOnComplete('completed');
    });
    await publish(started('B', 'human-b'));
    await act(async () => {
      mocks.observers.get('B').onChunk('B reply');
      for (const frame of animationFrames) frame();
    });

    expect(streamRef.current.liveTurnStore.getSnapshot()).toMatchObject({
      streamId: 'queued_B',
      content: 'B reply',
    });
    expect(streamRef.current.messages.filter((message) => message.content === 'A reply')).toHaveLength(1);
    expect(streamRef.current.messages).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'human-b', role: 'user' }),
      expect.objectContaining({ content: 'A reply', role: 'assistant' }),
    ]));
  });

  it('releases a stale B failure without clearing C pending text or generating state', async () => {
    await publish(started('B', 'human-b'));
    await act(async () => {
      mocks.observers.get('B').onChunk('B partial');
    });
    await publish(started('C', 'human-c'));
    await act(async () => {
      mocks.observers.get('C').onChunk('C first');
      mocks.observers.get('C').onChunk(' C pending');
      mocks.observers.get('B').onError(new Error('B failed'));
    });

    expect(streamRef.current.isGenerating).toBe(true);
    expect(mocks.reportFailure).not.toHaveBeenCalled();
    expect(streamRef.current.liveTurnStore.getSnapshot()).toMatchObject({
      streamId: 'queued_C',
      content: 'C first',
    });
    expect(animationFrames).toHaveLength(1);
    await act(async () => {
      animationFrames[0]();
    });
    expect(streamRef.current.liveTurnStore.getSnapshot()).toMatchObject({
      streamId: 'queued_C',
      content: 'C first C pending',
    });
    expect(streamRef.current.messages).toContainEqual(expect.objectContaining({
      id: 'B-assistant',
      responseStatus: 'failed',
      error: true,
    }));

    await act(async () => {
      mocks.observers.get('B').onChunk(' ignored after failure');
    });
    expect(streamRef.current.messages).not.toContainEqual(expect.objectContaining({
      id: 'B-assistant',
      content: expect.stringContaining('ignored after failure'),
    }));
  });

  it('releases client observers on navigation and reattaches without cancelling the execution', async () => {
    await publish(started('B', 'human-b'));
    const firstObserver = mocks.observers.get('B');

    await act(async () => root.unmount());
    host.remove();
    expect(firstObserver.cleanup).toHaveBeenCalledOnce();
    expect(mocks.cancelStream).not.toHaveBeenCalled();

    await mount();
    await publish(started('B', 'human-b'));

    expect(mocks.observeResponseStream).toHaveBeenCalledTimes(2);
    expect(mocks.observers.get('B')).not.toBe(firstObserver);
  });
});
