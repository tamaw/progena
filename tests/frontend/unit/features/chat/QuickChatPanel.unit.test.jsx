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
  generatingChatIds: [],
  requestIdByThreadId: {},
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
  selectGeneratingChatIds: () => mocks.generatingChatIds,
  selectRequestIdByThreadId: () => mocks.requestIdByThreadId,
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
  default: ({ onSend, disabled }) => (
    <button data-testid="quick-chat-send" type="button" disabled={disabled} onClick={() => { void onSend('Question'); }}>
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
import { defaultQuickChatConfig } from '@frontend/features/chat/models/quickChatConfig';
import { createQuickChatThreadTarget } from '@frontend/features/chat/models/quickChatThreadTarget';
import { BROWSER_EVENT } from '@frontend/lib/browserContracts';

let host;
let root;

async function mountQuickChat(panelConfig) {
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root.render(<QuickChatPanel onClose={vi.fn()} panelConfig={panelConfig} />);
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
  mocks.generatingChatIds = [];
  mocks.requestIdByThreadId = {};
  quickChatMemory.history = [];
  quickChatMemory.threadId = null;
  quickChatMemory.handoff = null;
  quickChatMemory.mode = null;
  quickChatMemory.adoptedTargetId = null;
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

describe('QuickChatPanel "Open in Quick Chat" thread adoption', () => {
  const targetConfig = (threadId) => ({
    ...defaultQuickChatConfig,
    threadTarget: createQuickChatThreadTarget({ threadId }),
  });
  const transcript = () => (latestChatMessages()?.messages || []).map((message) => message.content);
  const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });
  const rerender = (panelConfig) => act(async () => {
    root.render(<QuickChatPanel onClose={vi.fn()} panelConfig={panelConfig} />);
    await Promise.resolve();
  });

  beforeEach(() => {
    mocks.loadQuickChatHistory.mockResolvedValue([{ id: 'm1', role: 'user', content: 'Canonical hello' }]);
  });

  it('continues the requested canonical thread without sending or cancelling', async () => {
    await mountQuickChat(targetConfig('thread-a'));
    await flush();

    expect(mocks.loadQuickChatHistory).toHaveBeenCalledTimes(1);
    expect(quickChatMemory.threadId).toBe('thread-a');
    expect(transcript()).toEqual(['Canonical hello']);
    expect(mocks.sendMessageStream).not.toHaveBeenCalled();
    expect(mocks.cancelStream).not.toHaveBeenCalled();
    expect(host.querySelector('[data-testid="quick-chat-send"]').disabled).toBe(false);
  });

  it('sends the next message once on the adopted thread, not a clone', async () => {
    const request = pendingSend();
    await mountQuickChat(targetConfig('thread-a'));
    await flush();
    await sendQuickChat();

    expect(mocks.sendMessageStream).toHaveBeenCalledTimes(1);
    const [text, inputHistory, , , , , options] = mocks.sendMessageStream.mock.calls[0];
    // Page context may precede the typed text; it is never a replayed transcript.
    expect(text).toMatch(/(^|\n)Question$/);
    expect(text).not.toContain('Canonical hello');
    expect(inputHistory).toEqual([{ role: 'user', content: 'Canonical hello' }]);
    expect(options.thread_id).toBe('thread-a');

    await act(async () => {
      request.resolve();
      await Promise.resolve();
    });
  });

  it('replaces a scratch conversation and adopts each request only once', async () => {
    quickChatMemory.threadId = 'scratch-thread';
    quickChatMemory.mode = 'default';
    quickChatMemory.history = [{ id: 's1', role: 'user', content: 'Scratch' }];
    const config = targetConfig('thread-a');
    await mountQuickChat(config);
    await flush();
    expect(quickChatMemory.threadId).toBe('thread-a');
    expect(transcript()).toEqual(['Canonical hello']);

    mocks.loadQuickChatHistory.mockClear();
    await rerender({ ...config });
    await flush();
    expect(mocks.loadQuickChatHistory).not.toHaveBeenCalled();
    expect(quickChatMemory.threadId).toBe('thread-a');
  });

  it('switches to a newly requested thread', async () => {
    await mountQuickChat(targetConfig('thread-a'));
    await flush();
    mocks.loadQuickChatHistory.mockResolvedValue([{ id: 'm2', role: 'user', content: 'Other thread' }]);
    await rerender(targetConfig('thread-b'));
    await flush();
    expect(quickChatMemory.threadId).toBe('thread-b');
    expect(transcript()).toEqual(['Other thread']);
    expect(mocks.sendMessageStream).not.toHaveBeenCalled();
  });

  it('joins a running reply on the target thread instead of loading or sending', async () => {
    mocks.generatingChatIds = ['thread-a'];
    mocks.requestIdByThreadId = { 'thread-a': 'req-live' };
    mocks.observeResponseStream.mockReturnValue(() => {});
    await mountQuickChat(targetConfig('thread-a'));
    await flush();

    expect(mocks.observeResponseStream).toHaveBeenCalledWith('req-live', expect.any(Object));
    expect(mocks.loadQuickChatHistory).not.toHaveBeenCalled();
    expect(mocks.sendMessageStream).not.toHaveBeenCalled();
    expect(mocks.cancelStream).not.toHaveBeenCalled();
  });

  it('refuses to switch away while its own reply is streaming', async () => {
    const request = pendingSend();
    await mountQuickChat();
    await sendQuickChat();
    const ownThreadId = quickChatMemory.threadId;

    await rerender(targetConfig('thread-a'));
    await flush();
    expect(host.querySelector('[role="alert"]').textContent).toMatch(/still replying/);
    expect(quickChatMemory.threadId).toBe(ownThreadId);
    expect(mocks.cancelStream).not.toHaveBeenCalled();
    expect(mocks.sendMessageStream).toHaveBeenCalledTimes(1);

    await act(async () => {
      request.resolve();
      await Promise.resolve();
    });
  });

  it('shows a loading state and disables the composer while history loads', async () => {
    let resolveLoad;
    mocks.loadQuickChatHistory.mockReturnValue(new Promise((resolve) => { resolveLoad = resolve; }));
    await mountQuickChat(targetConfig('thread-a'));
    await flush();
    expect(host.querySelector('[role="status"]').textContent).toBe('Loading conversation…');
    expect(host.querySelector('[data-testid="quick-chat-send"]').disabled).toBe(true);

    await act(async () => { resolveLoad([{ id: 'm1', role: 'user', content: 'Loaded' }]); });
    await flush();
    expect(host.querySelector('[role="status"]')).toBeNull();
    expect(transcript()).toEqual(['Loaded']);
  });

  it('reports a failed load without sending anything', async () => {
    mocks.loadQuickChatHistory.mockRejectedValue(new Error('gone'));
    await mountQuickChat(targetConfig('thread-a'));
    await flush();
    expect(host.querySelector('[role="alert"]').textContent).toMatch(/Could not load that conversation/);
    expect(host.querySelector('[data-testid="quick-chat-send"]').disabled).toBe(false);
    expect(mocks.sendMessageStream).not.toHaveBeenCalled();
  });

  it('hands the adopted canonical thread back to Chat unchanged', async () => {
    await mountQuickChat(targetConfig('thread-a'));
    await flush();
    const handoffs = [];
    const listener = (event) => handoffs.push(event.detail);
    window.addEventListener(BROWSER_EVENT.LOAD_CHAT_HISTORY, listener);
    act(() => host.querySelector('button[label="Open in chat"]').click());
    window.removeEventListener(BROWSER_EVENT.LOAD_CHAT_HISTORY, listener);
    expect(handoffs).toHaveLength(1);
    expect(handoffs[0].threadId).toBe('thread-a');
    expect(handoffs[0].messages.map((message) => message.content)).toEqual(['Canonical hello']);
  });

  it('keeps a plain quick chat on its own scratch thread', async () => {
    await mountQuickChat(defaultQuickChatConfig);
    await flush();
    expect(mocks.loadQuickChatHistory).not.toHaveBeenCalled();
    expect(quickChatMemory.threadId).toBe('quick-id-1');
  });
});
