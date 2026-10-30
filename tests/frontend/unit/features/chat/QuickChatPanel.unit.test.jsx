// @vitest-environment jsdom
import React, { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRoot } from 'react-dom/client';

const mocks = vi.hoisted(() => ({
  sendMessageStream: vi.fn(),
  cancelStream: vi.fn(),
  observeResponseStream: vi.fn(),
  loadQuickChatHistory: vi.fn(),
  dispatch: vi.fn(),
  navigate: vi.fn(),
  chatMessageProps: [],
  nextUuid: 0,
}));

vi.mock('react-redux', () => ({
  useDispatch: () => mocks.dispatch,
  useSelector: (selector) => selector(),
}));
vi.mock('react-router-dom', () => ({
  useLocation: () => ({ pathname: '/chat' }),
  useNavigate: () => mocks.navigate,
}));
vi.mock('@frontend/context/ConfigContext', () => ({
  useConfig: () => ({
    config: { storage: { quickChatHandoff: 'quick-chat-handoff' } },
    getApiUrl: (path) => `/api/${path}`,
  }),
}));
vi.mock('@frontend/features/chat/hooks/useChatApi', () => ({
  useChatApi: () => ({
    sendMessageStream: mocks.sendMessageStream,
    cancelStream: mocks.cancelStream,
    observeResponseStream: mocks.observeResponseStream,
  }),
}));
vi.mock('@frontend/features/chat/state/chats', () => ({
  fetchChatMessages: vi.fn(),
  selectGeneratingChatIds: () => [],
  selectRequestIdByThreadId: () => ({}),
}));
vi.mock('@frontend/features/chat/models/quickChatStreamModel', () => ({
  loadQuickChatHistory: mocks.loadQuickChatHistory,
  toQuickChatInputHistory: (history) => history.map((turn) => ({
    role: turn.role,
    content: turn.inputContent ?? turn.content,
  })),
}));
vi.mock('@frontend/features/settings/state/preferences', () => ({
  savePreferences: vi.fn((payload) => payload),
  selectPreferences: () => ({ loaded: true, quickChatThreadId: null }),
  selectPreferencesSaveRequest: () => ({}),
}));
vi.mock('@frontend/features/settings/state/modelProfiles', () => ({
  selectModelProfiles: () => [],
}));
vi.mock('@frontend/realtime/realtimeSlice', () => ({
  selectUiEventQueue: () => [],
}));
vi.mock('@frontend/realtime/eventQueue', () => ({
  consumeUiEventQueue: (_queue, sequence) => sequence,
}));
vi.mock('@frontend/lib/uuid', () => ({
  createUuid: () => `quick-id-${++mocks.nextUuid}`,
}));
vi.mock('@frontend/features/chat/components/ChatInput', () => ({
  default: ({ onSend }) => (
    <button data-testid="quick-chat-send" type="button" onClick={() => { void onSend('Question'); }}>
      Send
    </button>
  ),
}));
vi.mock('@frontend/features/chat/components/ChatMessages', () => ({
  default: (props) => {
    mocks.chatMessageProps.push(props);
    return <div data-testid="quick-chat-messages" />;
  },
}));
vi.mock('@frontend/components/panels/PanelFrame', () => ({
  TopNavPane: ({ actions, children }) => <div>{actions}{children}</div>,
  PanelIconButton: ({ children, ...props }) => <button type="button" {...props}>{children}</button>,
}));
vi.mock('@frontend/components/navIcons', () => ({
  OpenChatIcon: () => null,
  QuickChatIcon: () => null,
}));

import { QuickChatPanel } from '@frontend/features/chat/QuickChatPanel';
import { quickChatMemory } from '@frontend/features/chat/models/quickChatMemory';

let host;
let root;

async function mountQuickChat() {
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root.render(<QuickChatPanel onClose={vi.fn()} />);
    await Promise.resolve();
  });
}

async function sendQuickChat() {
  await act(async () => {
    host.querySelector('[data-testid="quick-chat-send"]').click();
    await Promise.resolve();
    await Promise.resolve();
  });
}

function pendingSend() {
  let resolve;
  const terminal = new Promise((resolveTerminal) => {
    resolve = resolveTerminal;
  });
  mocks.sendMessageStream.mockImplementationOnce((...args) => {
    mocks.pendingCallbacks = {
      onComplete: args[3],
      onError: args[4],
    };
    return terminal;
  });
  return { resolve };
}

function latestChatMessages() {
  return mocks.chatMessageProps.at(-1);
}

beforeEach(() => {
  mocks.sendMessageStream.mockReset();
  mocks.cancelStream.mockReset();
  mocks.observeResponseStream.mockReset();
  mocks.loadQuickChatHistory.mockReset();
  mocks.dispatch.mockReset();
  mocks.navigate.mockReset();
  mocks.chatMessageProps.length = 0;
  mocks.nextUuid = 0;
  mocks.pendingCallbacks = null;
  quickChatMemory.history = [];
  quickChatMemory.threadId = null;
  quickChatMemory.handoff = null;
  quickChatMemory.mode = null;
  vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1));
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
});

afterEach(async () => {
  if (root) {
    await act(async () => root.unmount());
  }
  host?.remove();
  root = null;
  host = null;
  vi.unstubAllGlobals();
});

describe('QuickChatPanel committed-history reconciliation', () => {
  it('recovers a committed terminal marker while the panel stays open', async () => {
    const marker = {
      id: 'summary-1',
      anchor_message_id: 'assistant-1',
      summary: 'Earlier context.',
      scope: { kind: 'conversation', id: 'quick-id-1' },
    };
    const request = pendingSend();
    mocks.loadQuickChatHistory.mockImplementation(async (_fetchPage, isCurrent, onSummaryMarkers) => {
      expect(isCurrent()).toBe(true);
      onSummaryMarkers([marker]);
      return [{ id: 'assistant-1', role: 'assistant', content: 'Canonical answer' }];
    });

    await mountQuickChat();
    await sendQuickChat();
    await act(async () => {
      mocks.pendingCallbacks.onComplete('completed');
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mocks.loadQuickChatHistory).toHaveBeenCalledTimes(1);
    expect(latestChatMessages().summaryMarkers).toEqual([{
      id: 'summary-1',
      anchorMessageId: 'assistant-1',
      summary: 'Earlier context.',
      scope: { kind: 'conversation', id: 'quick-id-1' },
    }]);

    await act(async () => {
      request.resolve();
      await Promise.resolve();
    });
  });

  it.each([
    ['cancelled', (callbacks) => callbacks.onComplete('cancelled')],
    ['failed', (callbacks) => callbacks.onError(new Error('Response failed'))],
  ])('reconciles once after a %s terminal', async (_status, terminal) => {
    const request = pendingSend();
    mocks.loadQuickChatHistory.mockResolvedValue([]);

    await mountQuickChat();
    await sendQuickChat();
    await act(async () => {
      terminal(mocks.pendingCallbacks);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mocks.loadQuickChatHistory).toHaveBeenCalledTimes(1);

    await act(async () => {
      request.resolve();
      await Promise.resolve();
    });
  });

  it('discards a completed refresh after the next turn takes ownership', async () => {
    const firstRequest = pendingSend();
    let resolveRefresh;
    mocks.loadQuickChatHistory.mockImplementation(() => new Promise((resolve) => {
      resolveRefresh = resolve;
    }));

    await mountQuickChat();
    await sendQuickChat();
    await act(async () => {
      mocks.pendingCallbacks.onComplete('completed');
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(mocks.loadQuickChatHistory).toHaveBeenCalledTimes(1);

    await act(async () => {
      firstRequest.resolve();
      await Promise.resolve();
    });

    const secondRequest = pendingSend();
    await sendQuickChat();
    await act(async () => {
      resolveRefresh([{ id: 'stale-answer', role: 'assistant', content: 'Stale canonical answer' }]);
      await Promise.resolve();
    });

    expect(latestChatMessages().messages).not.toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'stale-answer' }),
    ]));

    await act(async () => {
      secondRequest.resolve();
      await Promise.resolve();
    });
  });
});
