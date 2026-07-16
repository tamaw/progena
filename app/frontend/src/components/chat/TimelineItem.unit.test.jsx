import React from 'react';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';

const renderers = vi.hoisted(() => ({
  renderSafeMarkdown: vi.fn(() => '<p><strong>formatted terminal content</strong></p>'),
  formatMessage: vi.fn(() => '<p><strong>legacy formatted terminal content</strong></p>'),
  sanitizeHtmlFragment: vi.fn((html) => html),
  describeStreamEvent: vi.fn(() => ({ badge: '', title: '', metaDetail: '', bodyHtml: '', bodyDetail: '' })),
}));

vi.mock('./messageRenderUtils', () => ({
  renderSafeMarkdown: renderers.renderSafeMarkdown,
  formatMessage: renderers.formatMessage,
  sanitizeHtmlFragment: renderers.sanitizeHtmlFragment,
  describeStreamEvent: renderers.describeStreamEvent,
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

    expect(renderers.renderSafeMarkdown).not.toHaveBeenCalled();
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

    expect(renderers.renderSafeMarkdown).toHaveBeenCalledOnce();
    expect(renderers.renderSafeMarkdown).toHaveBeenCalledWith('**literal markdown** <img src=x onerror=alert(1)>');
    expect(renderers.formatMessage).not.toHaveBeenCalled();
    expect(renderers.sanitizeHtmlFragment).not.toHaveBeenCalled();
    expect(terminalMarkup).toContain('<p><strong>formatted terminal content</strong></p>');
  });

  it('renders sanitized reasoning disclosure content without throwing', () => {
    const bodyHtml = '<strong>Reasoning summary</strong>';
    const reasoningDetailHtml = '<p>Reasoning detail</p>';
    renderers.describeStreamEvent.mockReturnValueOnce({
      badge: 'reasoning',
      title: 'Reasoning',
      metaDetail: '',
      bodyHtml,
      reasoningDetailHtml,
      bodyDetail: '',
    });
    renderers.sanitizeHtmlFragment
      .mockImplementationOnce(() => '<p>Sanitized reasoning summary</p>')
      .mockImplementationOnce(() => '<p>Sanitized reasoning detail</p>');

    let markup = '';
    expect(() => {
      markup = renderToStaticMarkup(React.createElement(TimelineItem, {
        item: {
          kind: 'stream_event',
          event: { id: 'reasoning-1', eventType: 'reasoning', timestamp: 1720000000000 },
        },
        itemIndex: 0,
      }));
    }).not.toThrow();

    expect(renderers.sanitizeHtmlFragment).toHaveBeenCalledWith(bodyHtml);
    expect(renderers.sanitizeHtmlFragment).toHaveBeenCalledWith(reasoningDetailHtml);
    expect(markup).toContain('reasoning-event-disclosure');
    expect(markup).toContain('<p>Sanitized reasoning summary</p>');
    expect(markup).toContain('<p>Sanitized reasoning detail</p>');
  });
});
