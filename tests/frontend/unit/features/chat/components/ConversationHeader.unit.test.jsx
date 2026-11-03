// @vitest-environment jsdom
import React, { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { LiveConversationHeader } from '@frontend/features/chat/components/ConversationHeader';
import { createLiveAssistantTurnStore } from '@frontend/features/chat/models/liveAssistantTurnStore';
import { createAiRenameAction } from '@frontend/features/chat/hooks/useAiRename';

function header(messages, cumulativeUsage) {
  return renderToStaticMarkup(<LiveConversationHeader
    liveTurnStore={createLiveAssistantTurnStore({ reasoningTokenCount: 999, reasoningText: 'Uncounted streamed reasoning' })}
    messages={messages} cumulativeUsage={cumulativeUsage} chatActions={[]}
    title="Accounting" chatStatus="idle" threadId="thread-accounting"
  />);
}

describe('conversation lifetime accounting', () => {
  it('keeps the authoritative total across visible pages and does not double count live reasoning', () => {
    const usage = { output_tokens: 78477, reasoning_tokens: 15780, output_coverage: 'complete', reasoning_coverage: 'complete' };
    for (const messages of [[], [{ id: 'latest', role: 'assistant', tokenCount: 65493 }], [{ role: 'assistant', tokenCount: 10 }, { role: 'assistant', tokenCount: 65493 }]]) {
      const markup = header(messages, usage);
      expect(markup).toContain('78,477 output tokens');
      expect(markup).toContain('15,780 reasoning tokens');
      expect(markup).not.toContain('16,779');
    }
  });

  it('keeps output unknown but displays zero for unreported reasoning without estimating text', () => {
    const markup = header([{ role: 'assistant', tokenCount: 7, reasoningText: 'a'.repeat(100) }], null);
    expect(markup).toContain('output tokens unavailable');
    expect(markup).toContain('0 reasoning tokens');
  });

  it('distinguishes incomplete recorded totals from complete provider zero', () => {
    const markup = header([], { output_tokens: 42, output_coverage: 'partial', reasoning_tokens: 0, reasoning_coverage: 'complete' });
    expect(markup).toContain('≥42 recorded output tokens');
    expect(markup).toContain('0 reasoning tokens');
  });
});

describe('LiveConversationHeader title and actions', () => {
  let root;
  let container;

  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function render({ title = 'Release planning', aiRename } = {}) {
    const onRequest = vi.fn();
    const actions = [
      aiRename ?? createAiRenameAction({ pending: false, blockedReason: null, onRequest }),
      { key: 'quick-chat', label: 'Open in Quick Chat', role: 'menuitem', onClick: () => {} },
      { key: 'delete', label: 'Delete', role: 'menuitem', danger: true, onClick: () => {} },
    ];
    act(() => root.render(
      <LiveConversationHeader
        liveTurnStore={createLiveAssistantTurnStore()}
        isRouteDetailError={false}
        messages={[]}
        cumulativeUsage={null}
        firstMessageTime={null}
        isContentLoading={false}
        isEmptyConversationPane={false}
        showEventFilters={false}
        streamFilters={{}}
        updateStreamFilters={() => {}}
        threadId="thread-a"
        chatStatus="idle"
        chatActions={actions}
        title={title}
      />,
    ));
    return { onRequest };
  }

  it('shows the title as read-only truncating text with no rename pencil', () => {
    render();
    const heading = container.querySelector('h1');
    expect(heading.textContent).toBe('Release planning');
    expect(heading.querySelector('.detail-header__title-text')).not.toBeNull();
    expect(heading.querySelector('button')).toBeNull();
    expect(heading.querySelector('input')).toBeNull();
    expect(container.querySelector('button[aria-label="Rename conversation"]')).toBeNull();
  });

  it('exposes AI Rename inline and in the overflow menu with the same handler and order', () => {
    const { onRequest } = render();
    const inline = container.querySelector('.detail-header-actions__inline button[aria-label="AI Rename"]');
    expect(inline).not.toBeNull();
    act(() => inline.click());
    expect(onRequest).toHaveBeenCalledTimes(1);

    act(() => container.querySelector('button[aria-label="Conversation actions"]').click());
    const items = [...container.querySelectorAll('[role="menu"] button')];
    expect(items.map((item) => item.textContent)).toEqual(['AI Rename', 'Open in Quick Chat', 'Delete']);
    act(() => items[0].click());
    expect(onRequest).toHaveBeenCalledTimes(2);
  });

  it('disables AI Rename in both renderings while blocked or pending', () => {
    render({ aiRename: createAiRenameAction({ pending: true, blockedReason: null, onRequest: vi.fn() }) });
    expect(container.querySelector('.detail-header-actions__inline button[aria-label="AI Rename"]').disabled).toBe(true);
    act(() => container.querySelector('button[aria-label="Conversation actions"]').click());
    const item = [...container.querySelectorAll('[role="menu"] button')].find((button) => button.textContent === 'AI Rename');
    expect(item.disabled).toBe(true);
  });
});
