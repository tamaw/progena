import { describe, expect, it, vi, afterEach, beforeAll, afterAll } from 'vitest';
import React from 'react';
import { Message } from './Message';

vi.mock('react', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    memo: (component) => component,
    useMemo: (factory) => factory(),
  };
});

vi.mock('./messageRenderUtils', () => ({
  formatMessage: (content) => content,
  sanitizeHtmlFragment: (content) => content,
  appendStreamingCursor: (content) => content,
  describeStreamEvent: () => ({ badge: '', title: '', metaDetail: '', bodyHtml: '', bodyDetail: '' }),
  badgeForEvent: () => ({ className: '', label: '' }),
  toolDetailsForEvent: () => null,
  todosDetailsForEvent: () => null,
  tokenMetaItemsForEvent: () => [],
  tagClassForEventType: () => '',
  formatTimestamp: () => '',
}));


const previousReact = globalThis.React;
beforeAll(() => {
  globalThis.React = React;
});
afterAll(() => {
  globalThis.React = previousReact;
});

function findByClass(node, className) {
  if (!node || typeof node !== 'object') return null;
  if (typeof node.type === 'function') {
    const match = findByClass(node.type(node.props), className);
    if (match) return match;
  }
  if (typeof node.props?.className === 'string' && node.props.className.split(' ').includes(className)) {
    return node;
  }
  const children = node.props?.children;
  if (Array.isArray(children)) {
    for (const child of children) {
      const match = findByClass(child, className);
      if (match) return match;
    }
  }
  return findByClass(children, className);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Message copy action', () => {
  it('writes the exact rendered message content to the browser clipboard', async () => {
    const clipboard = {
      value: null,
      async writeText(value) {
        this.value = value;
      },
    };
    vi.stubGlobal('navigator', { clipboard });

    const message = {
      role: 'assistant',
      content: 'Copy this answer exactly',
      timestamp: 1720000000000,
      streamEvents: [],
      timelineItems: undefined,
    };
    const rendered = Message({ message });
    const copyButton = findByClass(rendered, 'copy-btn');

    expect(copyButton).not.toBeNull();
    await copyButton.props.onClick();

    expect(clipboard.value).toBe('Copy this answer exactly');
  });
});
