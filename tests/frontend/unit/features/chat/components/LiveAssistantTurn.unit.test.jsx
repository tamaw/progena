import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const reactHarness = vi.hoisted(() => {
  let refs = [];
  let effects = [];
  let hookIndex = 0;

  return {
    beginRender() {
      hookIndex = 0;
      effects = [];
    },
    flushEffects() {
      effects.forEach((effect) => effect());
    },
    reset() {
      refs = [];
      effects = [];
      hookIndex = 0;
    },
    useEffect(effect) {
      effects.push(effect);
    },
    useRef(initialValue) {
      const index = hookIndex;
      hookIndex += 1;
      if (!refs[index]) {
        refs[index] = { current: initialValue };
      }
      return refs[index];
    },
  };
});

vi.mock('react', () => ({
  useLayoutEffect: reactHarness.useEffect,
  memo: (component) => component,
  useEffect: reactHarness.useEffect,
  useRef: reactHarness.useRef,
  useSyncExternalStore: (_subscribe, getSnapshot) => getSnapshot(),
}));

vi.mock('@frontend/features/chat/components/Message', () => ({
  Message: () => null,
}));

import LiveAssistantTurn from '@frontend/features/chat/components/LiveAssistantTurn';
import { createLiveAssistantTurnStore } from '@frontend/features/chat/models/liveAssistantTurnStore';

const originalGlobalReact = globalThis.React;

function streamingTurn() {
  return {
    streamId: 'stream-terminal-scroll',
    assistantMessageId: 'client-assistant-id',
    startedAt: 1000,
    assistantTimestamp: 1000,
    content: 'The response is now complete.',
    timelineItems: [{
      kind: 'assistant_message',
      id: 'client-assistant-id',
      content: 'The response is now complete.',
      timestamp: 1000,
      displayToolCalls: [],
      isStreaming: true,
      error: false,
    }],
    streamEvents: [],
    isStreaming: true,
    error: false,
  };
}

function renderLiveTurn({ store, container, pinned }) {
  reactHarness.beginRender();
  LiveAssistantTurn({
    store,
    messagesContainerRef: { current: container },
    isPinnedToBottomRef: { current: pinned },
    streamEventFilter: () => true,
    hideContent: false,
    showImages: true,
  });
  reactHarness.flushEffects();
}
beforeEach(() => {
  globalThis.React = { createElement: () => null };
});

afterEach(() => {
  globalThis.React = originalGlobalReact;
  reactHarness.reset();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// A transcript container with no messages: followTranscript only needs
// geometry and a selector query.
function fakeTranscript({ scrollTop, scrollHeight }) {
  return {
    scrollTop,
    scrollHeight,
    clientHeight: 300,
    querySelectorAll: () => [],
    getBoundingClientRect: () => ({ top: 0 }),
  };
}
describe('LiveAssistantTurn terminal outer-transcript scroll', () => {
  it('tails the pinned outer transcript synchronously when the real live store publishes its finalized snapshot', () => {
    const scheduledFrames = [];
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback) => {
      scheduledFrames.push(callback);
      return scheduledFrames.length;
    }));
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
    const store = createLiveAssistantTurnStore();
    const container = fakeTranscript({ scrollTop: 17, scrollHeight: 640 });
    const pinned = { current: true };
    const streaming = streamingTurn();

    store.set(streaming);
    renderLiveTurn({ store, container, pinned: pinned.current });
    expect(scheduledFrames).toHaveLength(1);
    expect(container.scrollTop).toBe(17);

    store.set({ ...streaming, isStreaming: false, assistantTimestamp: 2000 });
    renderLiveTurn({ store, container, pinned: pinned.current });

    expect(container.scrollTop).toBe(container.scrollHeight);
    expect(scheduledFrames).toHaveLength(1);
  });

  it('does not move an unpinned outer transcript when the real live store publishes its finalized snapshot', () => {
    const scheduledFrames = [];
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback) => {
      scheduledFrames.push(callback);
      return scheduledFrames.length;
    }));
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
    const store = createLiveAssistantTurnStore();
    const container = fakeTranscript({ scrollTop: 103, scrollHeight: 640 });
    const streaming = streamingTurn();

    store.set(streaming);
    renderLiveTurn({ store, container, pinned: false });
    store.set({ ...streaming, isStreaming: false, assistantTimestamp: 2000 });
    renderLiveTurn({ store, container, pinned: false });

    expect(container.scrollTop).toBe(103);
  });
});
