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
    requestChatSessionReconciliation: vi.fn((payload) => ({ type: 'ui/requestChatSessionReconciliation', payload })),
    fetchInboxCounts: vi.fn((payload) => ({ type: 'ui/fetchInboxCounts', payload })),
    fetchModelProfiles: vi.fn((payload) => ({ type: 'ui/fetchModelProfiles', payload })),
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

vi.mock('@frontend/context/ConfigContext', () => ({
  useConfig: () => ({ getApiUrl: bridge.getApiUrl }),
}));

vi.mock('@frontend/realtime/realtimeListener', () => ({
  startRealtimeStream: vi.fn((payload) => ({ type: 'realtime/start', payload })),
  stopRealtimeStream: vi.fn(() => ({ type: 'realtime/stop' })),
}));

vi.mock('@frontend/realtime/realtimeSlice', () => ({
  selectRealtimeConnected: vi.fn(() => bridge.connected),
  selectRealtimeStreamReady: vi.fn(() => bridge.connected),
}));

vi.mock("@frontend/features/chat/state/chats", () => ({
  requestChatSessionReconciliation: bridge.requestChatSessionReconciliation,
}));
vi.mock("@frontend/features/inbox/state", () => ({
  fetchInboxCounts: bridge.fetchInboxCounts
}));
vi.mock("@frontend/features/settings/state/modelProfiles", () => ({
  fetchModelProfiles: bridge.fetchModelProfiles
}));

import RealtimeBridge from '@frontend/store/RealtimeBridge';

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
    bridge.state.uiData.chats = { active: { threadId: null } };
    bridge.requestChatSessionReconciliation.mockClear();
    bridge.fetchInboxCounts.mockClear();
    bridge.fetchModelProfiles.mockClear();
    bridge.getApiUrl.mockClear();
  });

  afterEach(() => {
    bridge.resetHooks();
  });

  it('does not reconcile a chat on the first connection, and asks only for the inbox count and profiles', () => {
    renderBridge();

    expect(bridge.requestChatSessionReconciliation).not.toHaveBeenCalled();
    expect(snapshotCalls('ui/fetchInboxCounts')).toEqual([
      { type: 'ui/fetchInboxCounts', payload: { inboxUrl: '/api/ui/inbox' } },
    ]);
    expect(bridge.fetchModelProfiles).toHaveBeenCalledTimes(1);
  });

  it('does not reconcile when selecting chats while connected', () => {
    renderBridge();
    bridge.state.uiData.chats.active.threadId = 'chat-open';
    renderBridge();

    expect(bridge.requestChatSessionReconciliation).not.toHaveBeenCalled();
  });

  it('reconciles the open non-streaming chat and asks for the inbox count and profiles again on reconnect', () => {
    renderBridge();
    bridge.state.uiData.chats.active.threadId = 'chat-open';
    bridge.connected = false;
    renderBridge();
    bridge.connected = true;
    renderBridge();

    expect(bridge.fetchInboxCounts).toHaveBeenCalledTimes(2);
    expect(bridge.fetchModelProfiles).toHaveBeenCalledTimes(2);
    expect(snapshotCalls('ui/requestChatSessionReconciliation')).toEqual([
      {
        type: 'ui/requestChatSessionReconciliation',
        payload: { chatsUrl: '/api/ui/chats', threadId: 'chat-open' },
      },
    ]);
  });
});
