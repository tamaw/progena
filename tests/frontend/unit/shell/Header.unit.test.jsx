// @vitest-environment jsdom
import { act, createRef } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-redux', async () => {
  const alerts = await import('@frontend/features/alerts/state');
  const preferences = await import('@frontend/features/settings/state/preferences');
  const values = new Map([
    [alerts.selectUiAlerts, []],
    [preferences.selectAgentName, 'Progena'],
  ]);
  return { useSelector: (selector) => values.get(selector) };
});
vi.mock('@frontend/features/settings/useTheme', () => ({ useTheme: () => ({ currentTheme: 'dark', toggleTheme: () => {} }) }));
vi.mock('@frontend/shell/WorkspaceSwitcher', () => ({ WorkspaceSwitcher: () => <span>Workspace</span> }));

const { Header } = await import('@frontend/shell/Header');

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let root;
let container;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(fieldMode, handlers = {}) {
  const inputRef = createRef();
  const props = {
    activePanel: null,
    setActivePanel: vi.fn(),
    onSubmitQuickChat: vi.fn((event) => event.preventDefault()),
    onSubmitSearch: vi.fn((event) => event.preventDefault()),
    onOpenPanel: vi.fn(),
    onOpenQuickChat: vi.fn(),
    onToggleNav: vi.fn(),
    quickChatInputRef: inputRef,
    ...handlers,
  };
  act(() => root.render(<MemoryRouter><Header {...props} fieldMode={fieldMode} /></MemoryRouter>));
  return { props, inputRef };
}

const submit = (form) => act(() => form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));

describe('Header field modes', () => {
  it('Ask (default) submits to Quick Chat with the agent name', () => {
    const { props, inputRef } = render(undefined);
    const form = container.querySelector('form[aria-label="Ask Progena"]');
    expect(form).not.toBeNull();
    expect(inputRef.current.getAttribute('aria-label')).toBe('Ask Progena');
    submit(form);
    expect(props.onSubmitQuickChat).toHaveBeenCalledTimes(1);
    expect(props.onSubmitSearch).not.toHaveBeenCalled();
  });

  it('Search renders a search landmark that submits to search, not the AI', () => {
    const { props, inputRef } = render('search');
    const form = container.querySelector('form[role="search"]');
    expect(form.getAttribute('aria-label')).toBe('Search');
    expect(inputRef.current.name).toBe('query');
    expect(container.querySelector('form[aria-label="Ask Progena"]')).toBeNull();
    submit(form);
    expect(props.onSubmitSearch).toHaveBeenCalledTimes(1);
    expect(props.onSubmitQuickChat).not.toHaveBeenCalled();
    expect(container.textContent).toContain('CtrlQ');
  });

  it('Hidden removes only the field; Search and Quick chat buttons remain', () => {
    const { inputRef } = render('hidden');
    expect(container.querySelector('form')).toBeNull();
    expect(inputRef.current).toBeNull();
    expect(container.querySelector('button[aria-label="Search"]')).not.toBeNull();
    expect(container.querySelector('button[aria-label="Quick chat"]')).not.toBeNull();
    expect(container.querySelector('.app-shell__actions--end')).not.toBeNull();
  });
});
