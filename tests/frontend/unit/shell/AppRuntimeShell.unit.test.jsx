// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const shell = vi.hoisted(() => ({ fieldMode: 'ask', panelHost: null }));

vi.mock('react-redux', async () => {
  const realtime = await import('@frontend/realtime/realtimeSlice');
  const preferences = await import('@frontend/features/settings/state/preferences');
  const values = new Map([
    [realtime.selectRealtime, { connected: false }],
    [preferences.selectPreferences, { loaded: false }],
    [preferences.selectPreferencesSaveRequest, {}],
  ]);
  return { useDispatch: () => vi.fn(), useSelector: (selector) => values.get(selector) ?? null };
});
vi.mock('react-router-dom', () => ({ Outlet: () => null }));
vi.mock('@frontend/context/ConfigContext', () => ({
  useConfig: () => ({
    config: { headerField: { default: shell.fieldMode, options: ['ask', 'search', 'hidden'] } },
    getApiUrl: (path) => `/api/${path}`,
  }),
}));
vi.mock('@frontend/context/NotificationContext', () => ({ useNotifications: () => ({ show: vi.fn() }) }));
vi.mock('@frontend/components/NotificationContainer', () => ({ default: () => null }));
vi.mock('@frontend/shell/ConnectionRecoveryModal', () => ({ default: () => null }));
vi.mock('@frontend/shell/PrimaryNav', () => ({ PrimaryNav: () => null }));
// The real Header is covered by Header.unit.test.jsx; this one only forwards
// the field mode, ref and submit handler the shell supplies.
vi.mock('@frontend/shell/Header', () => ({
  Header: ({ fieldMode, onSubmitSearch, quickChatInputRef }) => (fieldMode === 'hidden' ? null : (
    <form data-testid="header-field" onSubmit={fieldMode === 'search' ? onSubmitSearch : (event) => event.preventDefault()}>
      <input ref={quickChatInputRef} name={fieldMode === 'search' ? 'query' : 'prompt'} />
    </form>
  )),
}));
vi.mock('@frontend/shell/PanelHost', () => ({
  PanelHost: (props) => {
    shell.panelHost = props;
    return null;
  },
}));

const { AppRuntimeShell } = await import('@frontend/shell/AppRuntimeShell');
const { BROWSER_EVENT } = await import('@frontend/lib/browserContracts');

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let root;
let container;

beforeEach(() => {
  shell.fieldMode = 'ask';
  shell.panelHost = null;
  localStorage.clear();
  window.matchMedia = () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const mount = () => act(() => root.render(<AppRuntimeShell />));
const pressCtrlQ = () => {
  const event = new KeyboardEvent('keydown', { key: 'q', ctrlKey: true, bubbles: true, cancelable: true });
  act(() => { document.dispatchEvent(event); });
  return event;
};
const openQuickChat = (detail) => act(() => {
  window.dispatchEvent(new CustomEvent(BROWSER_EVENT.OPEN_QUICK_CHAT, { detail }));
});

describe('AppRuntimeShell header field shortcuts', () => {
  it('Ctrl+Q focuses the visible header field without opening a panel', () => {
    mount();
    const event = pressCtrlQ();
    expect(event.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(container.querySelector('input[name="prompt"]'));
    expect(shell.panelHost.activePanel).toBeNull();
  });

  it('Ctrl+Q still reaches Ask through Quick Chat when the field is hidden', () => {
    shell.fieldMode = 'hidden';
    mount();
    const event = pressCtrlQ();
    expect(event.defaultPrevented).toBe(true);
    expect(container.querySelector('input')).toBeNull();
    expect(shell.panelHost.activePanel).toBe('quickChat');
    expect(shell.panelHost.quickChatConfig.mode).toBe('default');
    expect(shell.panelHost.quickChatSubmission).toBeNull();
  });
});

describe('AppRuntimeShell header Search mode', () => {
  it('opens the search panel with the trimmed query and never submits to Quick Chat', () => {
    shell.fieldMode = 'search';
    mount();
    const form = container.querySelector('[data-testid="header-field"]');
    const input = form.querySelector('input[name="query"]');
    const submit = (value) => {
      input.value = value;
      act(() => { form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); });
    };

    submit('  release notes  ');
    expect(shell.panelHost.activePanel).toBe('search');
    expect(shell.panelHost.searchQuery).toBe('release notes');
    const firstKey = shell.panelHost.searchKey;
    expect(input.value).toBe('');

    submit('roadmap');
    expect(shell.panelHost.searchQuery).toBe('roadmap');
    expect(shell.panelHost.searchKey).not.toBe(firstKey);
    expect(shell.panelHost.quickChatSubmission).toBeNull();
    expect(shell.panelHost.quickChatDraft).toBeNull();
  });
});

describe('AppRuntimeShell "Open in Quick Chat" requests', () => {
  it('opens Quick Chat on the named canonical thread without a prompt submission', () => {
    mount();
    openQuickChat({ threadId: 'thread-a' });
    expect(shell.panelHost.activePanel).toBe('quickChat');
    expect(shell.panelHost.quickChatConfig.mode).toBe('default');
    expect(shell.panelHost.quickChatConfig.threadTarget.threadId).toBe('thread-a');
    expect(shell.panelHost.quickChatSubmission).toBeNull();
    expect(shell.panelHost.quickChatDraft).toBeNull();

    const firstTarget = shell.panelHost.quickChatConfig.threadTarget;
    openQuickChat({ threadId: 'thread-a' });
    expect(shell.panelHost.quickChatConfig.threadTarget.id).not.toBe(firstTarget.id);
  });

  it('falls back to a plain quick chat for an invalid thread id', () => {
    mount();
    openQuickChat({ threadId: '../not a thread' });
    expect(shell.panelHost.activePanel).toBe('quickChat');
    expect(shell.panelHost.quickChatConfig.threadTarget).toBeUndefined();
    expect(shell.panelHost.quickChatSubmission).toBeNull();
  });
});
