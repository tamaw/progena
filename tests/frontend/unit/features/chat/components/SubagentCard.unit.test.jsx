import React from 'react';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';

const renderers = vi.hoisted(() => ({
  renderSafeMarkdown: vi.fn(() => '<pre><code class="language-js">const answer = 42;</code></pre>'),
  formatMessage: vi.fn(() => '<pre><code class="language-js">legacy answer</code></pre>'),
  sanitizeHtmlFragment: vi.fn((html) => html),
  appendStreamingCursor: vi.fn((html) => `${html}<span class="stream-cursor" aria-hidden="true">▋</span>`),
}));

vi.mock("@frontend/lib/safeMarkdown", () => ({
  renderSafeMarkdown: renderers.renderSafeMarkdown,
  formatMessage: renderers.formatMessage,
  sanitizeHtmlFragment: renderers.sanitizeHtmlFragment
}));
vi.mock("@frontend/features/chat/models/messageRenderUtils", () => ({
  appendStreamingCursor: renderers.appendStreamingCursor,
  formatArgsPreview: () => '',
  formatToolInputSummary: () => '',
  formatDuration: () => '',
  formatTimestamp: () => '',
  statusPillProps: () => ({
    label: 'Running',
    tone: 'pending'
  })
}));

import SubagentCard from '@frontend/features/chat/components/SubagentCard';

const previousReact = globalThis.React;
beforeAll(() => {
  globalThis.React = React;
});
afterAll(() => {
  globalThis.React = previousReact;
});

function subagentItem(overrides = {}) {
  return {
    id: 'subagent-1',
    kind: 'subagent',
    status: 'running',
    subagentName: 'researcher',
    entries: [{
      kind: 'message',
      id: 'subagent-message-1',
      text: '```js\nconst answer = 42;\n```',
    }],
    ...overrides,
  };
}

describe('SubagentCard message entries', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders a running trailing fenced code block literally with a cursor, then formats the settled entry', () => {
    const streamingMarkup = renderToStaticMarkup(React.createElement(SubagentCard, {
      item: subagentItem(),
    }));

    expect(streamingMarkup).toContain('```js\nconst answer = 42;\n```');
    expect(streamingMarkup).toContain('▋');
    expect(streamingMarkup).not.toContain('<pre><code class="language-js">');

    const settledMarkup = renderToStaticMarkup(React.createElement(SubagentCard, {
      item: subagentItem({ status: 'success' }),
    }));

    expect(settledMarkup).toContain('<pre><code class="language-js">const answer = 42;</code></pre>');
  });
});
