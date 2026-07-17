import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const bridge = vi.hoisted(() => {
  const hooks = { cursor: 0, effects: [], refs: [] };

  return {
    connected: true,
    state: {
      uiData: {
        chatRuntime: {
          generatingChatIds: [],
          responseThreadsByRequestId: {},
        },
      },
    },
    dispatch: vi.fn(),
    fetchChatSessions: vi.fn((payload) => ({ type: 'ui/fetchChatSessions', payload })),
    fetchInbox: vi.fn((payload) => ({ type: 'ui/fetchInbox', payload })),
    getApiUrl: vi.fn((path) => `/api/ui/${path}`),
    hooks,
    resetHooks() {
      for (const effect of hooks.effects) {
        effect?.cleanup?.();
      }
      hooks.cursor = 0;
      hooks.effects = [];
      hooks.refs = [];
    },
  };
});

vi.mock('react', () => ({
  useEffect(effect, dependencies) {
    const index = bridge.hooks.cursor;
    bridge.hooks.cursor += 1;
    const previous = bridge.hooks.effects[index];
    const changed = !previous
      || dependencies.length !== previous.dependencies.length
      || dependencies.some((dependency, dependencyIndex) => (
        dependency !== previous.dependencies[dependencyIndex]
      ));

    if (changed) {
      previous?.cleanup?.();
      bridge.hooks.effects[index] = {
        dependencies,
        cleanup: effect(),
      };
    }
  },
  useRef(initialValue) {
    const index = bridge.hooks.cursor;
    bridge.hooks.cursor += 1;
    if (!bridge.hooks.refs[index]) {
      bridge.hooks.refs[index] = { current: initialValue };
    }
    return bridge.hooks.refs[index];
  },
}));

vi.mock('react-redux', () => ({
  useDispatch: () => bridge.dispatch,
  useSelector: (selector) => selector(bridge.state),
}));

vi.mock('../context/ConfigContext', () => ({
  useConfig: () => ({ getApiUrl: bridge.getApiUrl }),
}));

vi.mock('./realtimeListener', () => ({
  startRealtimeStream: vi.fn((payload) => ({ type: 'realtime/start', payload })),
  stopRealtimeStream: vi.fn(() => ({ type: 'realtime/stop' })),
}));

vi.mock('./realtimeSlice', () => ({
  selectRealtimeConnected: vi.fn(() => bridge.connected),
}));

vi.mock('./uiDataSlice', () => ({
  fetchChatSessions: bridge.fetchChatSessions,
  fetchInbox: bridge.fetchInbox,
  selectGeneratingChatIds: vi.fn((state) => state?.uiData?.chatRuntime?.generatingChatIds || []),
}));

import RealtimeBridge from './RealtimeBridge';

function renderBridge() {
  bridge.hooks.cursor = 0;
  RealtimeBridge();
}

function snapshotCalls(action) {
  return bridge.dispatch.mock.calls
    .map(([dispatched]) => dispatched)
    .filter((dispatched) => dispatched?.type === action);
}

describe('RealtimeBridge reconnect recovery', () => {
  beforeEach(() => {
    bridge.resetHooks();
    bridge.connected = true;
    bridge.state.uiData.chatRuntime.generatingChatIds = [];
    bridge.state.uiData.chatRuntime.responseThreadsByRequestId = {};
    bridge.fetchChatSessions.mockClear();
    bridge.fetchInbox.mockClear();
    bridge.getApiUrl.mockClear();
  });

  afterEach(() => {
    bridge.resetHooks();
  });

  it('defers chat recovery during a generating reconnect while keeping inbox recovery immediate', () => {
    renderBridge();

    expect(snapshotCalls('ui/fetchChatSessions')).toEqual([
      { type: 'ui/fetchChatSessions', payload: { chatsUrl: '/api/ui/chats', includeArchived: true } },
    ]);
    expect(snapshotCalls('ui/fetchInbox')).toEqual([
      { type: 'ui/fetchInbox', payload: { inboxUrl: '/api/ui/inbox' } },
    ]);

    // The reconnect must reset both recovery guards, but only the lightweight
    // inbox snapshot is allowed while streamed chat generation is active.
    bridge.connected = false;
    renderBridge();
    bridge.state.uiData.chatRuntime.generatingChatIds = ['chat-in-progress'];
    bridge.connected = true;
    renderBridge();

    expect(bridge.fetchChatSessions).toHaveBeenCalledTimes(1);
    expect(bridge.fetchInbox).toHaveBeenCalledTimes(2);
    expect(snapshotCalls('ui/fetchInbox')).toHaveLength(2);

    // Once token painting is no longer active, the deferred authoritative
    // roster is fetched once and remains deduplicated for this connection.
    bridge.state.uiData.chatRuntime.generatingChatIds = [];
    renderBridge();
    expect(bridge.fetchChatSessions).toHaveBeenCalledTimes(2);
    expect(bridge.fetchChatSessions).toHaveBeenLastCalledWith({
      chatsUrl: '/api/ui/chats',
      includeArchived: true,
    });
    renderBridge();
    expect(bridge.fetchChatSessions).toHaveBeenCalledTimes(2);

    // A later same-URL reconnect is still a fresh EventSource lifetime and
    // therefore requires both authoritative baselines again.
    bridge.connected = false;
    renderBridge();
    bridge.connected = true;
    renderBridge();

    expect(bridge.fetchChatSessions).toHaveBeenCalledTimes(3);
    expect(bridge.fetchInbox).toHaveBeenCalledTimes(3);
    expect(snapshotCalls('ui/fetchChatSessions')).toHaveLength(3);
    expect(snapshotCalls('ui/fetchInbox')).toHaveLength(3);
  });

  it('defers chat recovery while a foreground response is tracked through canonical lifecycle state', () => {
    renderBridge();

    expect(snapshotCalls('ui/fetchChatSessions')).toEqual([
      { type: 'ui/fetchChatSessions', payload: { chatsUrl: '/api/ui/chats', includeArchived: true } },
    ]);
    expect(snapshotCalls('ui/fetchInbox')).toEqual([
      { type: 'ui/fetchInbox', payload: { inboxUrl: '/api/ui/inbox' } },
    ]);

    bridge.connected = false;
    renderBridge();
    bridge.state.uiData.chatRuntime.responseThreadsByRequestId = { req_1: 'chat-active' };
    bridge.connected = true;
    renderBridge();

    expect(bridge.fetchChatSessions).toHaveBeenCalledTimes(1);
    expect(bridge.fetchInbox).toHaveBeenCalledTimes(2);

    bridge.state.uiData.chatRuntime.responseThreadsByRequestId = {};
    renderBridge();

    expect(bridge.fetchChatSessions).toHaveBeenCalledTimes(2);
    expect(bridge.fetchChatSessions).toHaveBeenLastCalledWith({
      chatsUrl: '/api/ui/chats',
      includeArchived: true,
    });
  });
});
