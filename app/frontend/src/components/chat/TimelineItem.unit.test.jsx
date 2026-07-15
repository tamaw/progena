import React from 'react';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';

const renderers = vi.hoisted(() => ({
  formatMessage: vi.fn(() => '<p><strong>formatted terminal content</strong></p>'),
  sanitizeHtmlFragment: vi.fn((html) => html),
}));

vi.mock('./messageRenderUtils', () => ({
  formatMessage: renderers.formatMessage,
  sanitizeHtmlFragment: renderers.sanitizeHtmlFragment,
  describeStreamEvent: () => ({ badge: '', title: '', metaDetail: '', bodyHtml: '', bodyDetail: '' }),
  badgeForEvent: () => ({ className: '', label: '' }),
  toolDetailsForEvent: () => null,
  todosDetailsForEvent: () => null,
  tokenMetaItemsForEvent: () => [],
  tagClassForEventType: () => '',
  formatTimestamp: () => '',
}));

import TimelineItem from './TimelineItem';

const previousReact = globalThis.React;
beforeAll(() => {
  globalThis.React = React;
});
afterAll(() => {
  globalThis.React = previousReact;
});

function assistantItem(overrides = {}) {
  return {
    kind: 'assistant_message',
    id: 'assistant-1',
    content: '**literal markdown** <img src=x onerror=alert(1)>',
    timestamp: 1720000000000,
    displayToolCalls: [],
    isStreaming: true,
    error: false,
    ...overrides,
  };
}

describe('TimelineItem assistant messages', () => {
  it('renders live markdown-like content as escaped literal text without formatting, then formats the terminal item', () => {
    const streamingMarkup = renderToStaticMarkup(React.createElement(TimelineItem, {
      item: assistantItem(),
      itemIndex: 0,
    }));

    expect(renderers.formatMessage).not.toHaveBeenCalled();
    expect(renderers.sanitizeHtmlFragment).not.toHaveBeenCalled();
    expect(streamingMarkup).toContain('message-content--streaming-text');
    expect(streamingMarkup).toContain('**literal markdown**');
    expect(streamingMarkup).toContain('&lt;img src=x onerror=alert(1)&gt;');
    expect(streamingMarkup).not.toContain('<img src=x onerror=alert(1)>');
    expect(streamingMarkup).toContain('<span class="stream-cursor" aria-hidden="true">▋</span>');

    const terminalMarkup = renderToStaticMarkup(React.createElement(TimelineItem, {
      item: assistantItem({ isStreaming: false }),
      itemIndex: 0,
    }));

    expect(renderers.formatMessage).toHaveBeenCalledOnce();
    expect(renderers.formatMessage).toHaveBeenCalledWith('**literal markdown** <img src=x onerror=alert(1)>');
    expect(renderers.sanitizeHtmlFragment).toHaveBeenCalledOnce();
    expect(renderers.sanitizeHtmlFragment).toHaveBeenCalledWith('<p><strong>formatted terminal content</strong></p>');
    expect(terminalMarkup).toContain('<p><strong>formatted terminal content</strong></p>');
  });
});
