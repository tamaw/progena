import { afterEach, afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { Message } from './Message';

const actionHarness = {
  feedback: '',
};

vi.mock('react', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    memo: (component) => component,
    useMemo: (factory) => factory(),
    useState: (initialValue) => [actionHarness.feedback || initialValue, (nextValue) => {
      actionHarness.feedback = typeof nextValue === 'function'
        ? nextValue(actionHarness.feedback)
        : nextValue;
    }],
    useRef: (initialValue) => ({ current: initialValue }),
    useCallback: (callback) => callback,
    useEffect: () => {},
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
  let component = node.type;
  while (component && typeof component === 'object' && typeof component.type !== 'undefined') {
    component = component.type;
  }
  component = typeof component === 'function' ? component : null;
  if (component) {
    const match = findByClass(component(node.props), className);
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

function assistantMessage(content, timelineBacked) {
  if (!timelineBacked) {
    return {
      role: 'assistant',
      content,
      timestamp: 1720000000000,
      streamEvents: [],
      timelineItems: undefined,
    };
  }

  return {
    role: 'assistant',
    content,
    timestamp: 1720000000000,
    streamEvents: [],
    timelineItems: [{
      kind: 'assistant_message',
      id: 'timeline-assistant',
      content,
      timestamp: 1720000000000,
      tokenCount: 0,
      isStreaming: false,
      error: false,
    }],
  };
}

function renderAssistantActions(message, onRegenerate) {
  return Message({ message, onRegenerate, regenerateDisabled: false });
}

afterEach(() => {
  actionHarness.feedback = '';
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('assistant message actions', () => {
  it('copies a direct assistant response and acknowledges the completed copy', async () => {
    const clipboard = {
      text: null,
      writeText(value) {
        this.text = value;
        return Promise.resolve();
      },
    };
    vi.stubGlobal('navigator', { clipboard });
    const message = assistantMessage('Copy this answer exactly', false);

    const copyButton = findByClass(renderAssistantActions(message), 'copy-btn');
    const copyLabel = findByClass(copyButton, 'msg-action-btn__label');

    expect(copyButton?.props['aria-label']).toBe('Copy message');
    expect(copyButton?.props.className).toContain('msg-action-btn');
    expect(copyLabel?.props.children).toBe('Copy');
    expect(copyButton?.props['data-tooltip']).toBeUndefined();

    await copyButton.props.onClick();

    const acknowledgedCopyButton = findByClass(renderAssistantActions(message), 'copy-btn');
    expect(clipboard.text).toBe('Copy this answer exactly');
    expect(acknowledgedCopyButton?.props.className).toContain('is-feedback-visible');
    expect(acknowledgedCopyButton?.props['data-tooltip']).toBe('Copied');
  });

  it('regenerates a direct assistant response and acknowledges completion', () => {
    const regeneration = { count: 0 };
    const onRegenerate = () => {
      regeneration.count += 1;
    };
    const message = assistantMessage('Answer to regenerate', false);

    const regenerateButton = findByClass(renderAssistantActions(message, onRegenerate), 'regen-btn');
    const regenerateLabel = findByClass(regenerateButton, 'msg-action-btn__label');

    expect(regenerateButton?.props['aria-label']).toBe('Regenerate response');
    expect(regenerateLabel?.props.children).toBe('Regenerate');

    regenerateButton.props.onClick();

    const acknowledgedRegenerateButton = findByClass(
      renderAssistantActions(message, onRegenerate),
      'regen-btn',
    );
    expect(regeneration.count).toBe(1);
    expect(acknowledgedRegenerateButton?.props.className).toContain('is-feedback-visible');
    expect(acknowledgedRegenerateButton?.props['data-tooltip']).toBe('Regenerated');
  });

  it('offers the same labeled expandable controls from a timeline assistant row', () => {
    const message = assistantMessage('Timeline response', true);
    const markup = renderToStaticMarkup(React.createElement(Message, {
      message,
      onRegenerate: () => {},
      regenerateDisabled: false,
    }));

    expect(markup).toContain('class="msg-action-btn copy-btn"');
    expect(markup).toContain('aria-label="Copy message"');
    expect(markup).toContain('class="msg-action-btn__label">Copy</span>');
    expect(markup).toContain('class="msg-action-btn regen-btn"');
    expect(markup).toContain('aria-label="Regenerate response"');
    expect(markup).toContain('class="msg-action-btn__label">Regenerate</span>');
  });
});
