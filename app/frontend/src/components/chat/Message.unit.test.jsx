import React from 'react';
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { Message } from './Message';
import MessageImageStrip from './MessageImageStrip';

// Ensure React is in global scope for the component's JSX compilation
// when the automatic JSX runtime import is not injected by vitest.
if (typeof globalThis.React === 'undefined') {
  globalThis.React = React;
}

// ---- Mock the markdown module ----
vi.mock('../../lib/markdown', () => ({
  renderMarkdownToHtml: (content) => {
    if (!content) return '';
    const lines = content.split('\n').filter(Boolean);
    return lines.map((l) => `<p>${l}</p>`).join('\n');
  },
  extractMarkdownImages: () => [],
}));

// ---- Minimal DOM stubs ----
// MessageView uses sanitizeHtmlFragment which calls document.createElement
// and checks Node.ELEMENT_NODE. In Node 26, neither document nor Node are
// globals, so we provide minimal stubs that pass instanceof checks.
let savedDocument;
let savedNode;

// Node must be a constructor so that React/vitest internal instanceof
// checks (e.g. "x instanceof Node") don't throw.
function MockNode() {}
MockNode.ELEMENT_NODE = 1;
MockNode.TEXT_NODE = 3;

class MockElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this._children = [];
    this._attrs = new Map();
    this.textContent = '';
    this._innerHTML = '';
    this.parentNode = null;
  }

  get innerHTML() {
    if (this._innerHTML) return this._innerHTML;
    return this._children.map((c) => (typeof c === 'string' ? c : c.outerHTML || c.textContent || '')).join('');
  }

  set innerHTML(v) {
    this._innerHTML = v;
    this._children = [];
  }

  get content() {
    return this;
  }

  get childNodes() {
    return this._children;
  }

  get attributes() {
    return Array.from(this._attrs.entries()).map(([name, value]) => ({ name, value }));
  }

  appendChild(child) {
    child.parentNode = this;
    this._children.push(child);
    return child;
  }

  removeAttribute(name) {
    this._attrs.delete(name);
  }

  cloneNode(deep) {
    const c = new MockElement(this.tagName);
    c._innerHTML = this._innerHTML;
    c.textContent = this.textContent;
    c._children = deep !== false
      ? this._children.map((ch) => (typeof ch === 'string' ? ch : ch.cloneNode(deep)))
      : [];
    return c;
  }

  replaceWith(...nodes) {
    const parent = this.parentNode;
    if (!parent) return;
    const idx = parent._children.indexOf(this);
    if (idx !== -1) {
      parent._children.splice(idx, 1, ...nodes);
    }
  }

  get nodeType() {
    return this.tagName ? MockNode.ELEMENT_NODE : MockNode.TEXT_NODE;
  }

  get outerHTML() {
    const attrs = Array.from(this._attrs.entries())
      .map(([k, v]) => (v ? ` ${k}="${v}"` : ` ${k}`))
      .join('');
    return `<${this.tagName.toLowerCase()}${attrs}>${this.innerHTML}</${this.tagName.toLowerCase()}>`;
  }
}

class MockTextNode {
  constructor(text) {
    this._text = text;
    this.nodeType = MockNode.TEXT_NODE;
  }

  get textContent() {
    return this._text;
  }

  cloneNode() {
    return new MockTextNode(this._text);
  }
}

const mockDoc = {
  createElement(tag) {
    return new MockElement(tag);
  },
  createTextNode(text) {
    return new MockTextNode(text);
  },
};

const MS_EPOCH = 1720000000000;

beforeAll(() => {
  savedDocument = globalThis.document;
  savedNode = globalThis.Node;
  globalThis.Node = MockNode;
  globalThis.document = mockDoc;
});

afterAll(() => {
  globalThis.document = savedDocument;
  globalThis.Node = savedNode;
});

function baseMessage(overrides = {}) {
  return {
    id: 'm1',
    role: 'assistant',
    content: 'Hello world',
    timestamp: MS_EPOCH,
    isStreaming: false,
    error: false,
    isScheduled: false,
    streamEvents: [],
    tokenCount: 42,
    reasoningTokenCount: 0,
    reasoningText: '',
    model: '',
    displayAgentName: '',
    displayModelName: '',
    displayToolCalls: [],
    timelineItems: undefined,
    ...overrides,
  };
}
function divMarkupByClass(markup, className) {
  const openingTag = `<div class="${className}">`;
  const start = markup.indexOf(openingTag);
  if (start === -1) return '';

  const divTag = /<\/?div\b[^>]*>/g;
  divTag.lastIndex = start;
  let depth = 0;
  let match;
  while ((match = divTag.exec(markup))) {
    depth += match[0].startsWith('</') ? -1 : 1;
    if (depth === 0) return markup.slice(start, divTag.lastIndex);
  }
  return '';
}


/* ------------------------------------------------------------------ */
/*  timelineItems                                                      */
/* ------------------------------------------------------------------ */

describe('Message with timelineItems', () => {
  it('renders items in array order', () => {
    const message = baseMessage({
      timelineItems: [
        {
          kind: 'stream_event',
          id: 'e1',
          event: {
            id: 'e1',
            eventType: 'tool_start',
            content: '',
            metadata: { tool_name: 'search_web' },
          },
        },
        {
          kind: 'assistant_message',
          id: 'a1',
          content: 'I found results.',
          timestamp: MS_EPOCH,
          displayAgentName: '',
          displayModelName: '',
          displayToolCalls: ['search_web'],
          tokenCount: 10,
          reasoningTokenCount: 0,
          reasoningText: '',
          isStreaming: false,
          error: false,
        },
        {
          kind: 'stream_event',
          id: 'e2',
          event: {
            id: 'e2',
            eventType: 'tool_result',
            content: '',
            metadata: { tool_name: 'search_web', result_preview: '3 hits', duration_ms: 1200 },
          },
        },
      ],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));

    expect(html).toContain('chat-entry-chain--connected');

    const toolStartPos = html.indexOf('→ Search Web');
    const assistantPos = html.indexOf('I found results.');
    const toolResultPos = html.indexOf('tool_result');

    expect(toolStartPos).toBeGreaterThan(0);
    expect(assistantPos).toBeGreaterThan(toolStartPos);
    expect(toolResultPos).toBeGreaterThan(assistantPos);
  });
  it('renders streamed assistant content once in the explicit timeline row', () => {
    const streamedContent = 'Live assistant response';
    const message = baseMessage({
      content: streamedContent,
      isStreaming: true,
      timelineItems: [{
        kind: 'assistant_message',
        id: 'assistant-live',
        content: streamedContent,
        timestamp: MS_EPOCH,
        displayAgentName: '',
        displayModelName: '',
        displayToolCalls: [],
        tokenCount: 0,
        reasoningTokenCount: 0,
        reasoningText: '',
        isStreaming: true,
        error: false,
      }],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));
    const timelineItem = divMarkupByClass(html, 'chat-entry-chain--item');
    const messageEntries = html.match(/class="entry__main msg__entry-main"/g) || [];

    expect(timelineItem).toContain('class="entry__main msg__entry-main"');
    expect(timelineItem).toContain(streamedContent);
    expect(messageEntries).toHaveLength(1);
  });

  it('hides assistant content and images while retaining filtered timeline events', () => {
    const message = baseMessage({
      content: 'Should be hidden',
      streamEvents: [{
        id: 'e-hidden-content',
        eventType: 'tool_result',
        metadata: { tool_name: 'search_web', result_preview: 'retained event' },
        images: [{ id: 'image-1', base64: 'AAA', media_type: 'image/png' }],
      }],
      timelineItems: [
        {
          kind: 'stream_event',
          id: 'e-hidden-content',
          event: {
            id: 'e-hidden-content',
            eventType: 'tool_result',
            metadata: { tool_name: 'search_web', result_preview: 'retained event' },
          },
        },
        {
          kind: 'assistant_message',
          id: 'assistant-hidden-content',
          content: 'Should be hidden',
          timestamp: MS_EPOCH,
          displayAgentName: '',
          displayModelName: '',
          displayToolCalls: [],
          tokenCount: 0,
          reasoningTokenCount: 0,
          reasoningText: '',
          isStreaming: false,
          error: false,
        },
      ],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message, hideContent: true }));

    expect(html).not.toContain('Should be hidden');
    expect(html).toContain('retained event');
    expect(html).not.toContain('msg-img-strip');
  });

  it('exposes image thumbnails as keyboard-activatable controls', () => {
    const html = renderToStaticMarkup(React.createElement(MessageImageStrip, {
      images: [{ id: 'image-1', src: 'https://cdn.example.test/image.png', alt: 'Generated result' }],
    }));

    expect(html).toContain('role="button"');
    expect(html).toContain('tabindex="0"');
    expect(html).toContain('aria-label="View image 1 full size"');
    expect(html).toContain('alt="Generated result"');
  });
  it('renders a submitted task summary entry before the subagent final result', () => {
    const message = baseMessage({
      timelineItems: [{
        kind: 'subagent',
        id: 'subagent-call-summary',
        toolCallId: 'call-summary',
        subagentName: 'researcher',
        taskSummary: 'Read the README.md and summarize its purpose',
        status: 'success',
        startedAt: MS_EPOCH,
        finishedAt: MS_EPOCH + 100,
        durationMs: 100,
        resultPreview: 'Final answer from the researcher',
        error: null,
        entries: [],
      }],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));
    const bodyPos = html.indexOf('subagent-card__body');
    const summaryEntryPos = html.indexOf('subagent-card__task-summary-entry');
    const summaryPos = html.indexOf('Read the README.md and summarize its purpose');
    const resultPos = html.indexOf('Final answer from the researcher');

    expect(bodyPos).toBeGreaterThan(-1);
    expect(summaryEntryPos).toBeGreaterThan(bodyPos);
    expect(summaryPos).toBeGreaterThan(summaryEntryPos);
    expect(resultPos).toBeGreaterThan(summaryPos);
    expect(html).toContain('Read the README.md and summarize its purpose');
    expect(html.slice(summaryEntryPos, resultPos)).toMatch(/<svg[^>]*aria-hidden="true"/);
    expect(html.slice(summaryEntryPos, resultPos)).toContain('message-stream-event-body');
    expect(divMarkupByClass(html, 'subagent-card__children')).toMatch(
      /Read the README\.md and summarize its purpose[\s\S]*Final answer from the researcher/,
    );
  });

  it('omits the task summary line when persisted metadata has no description', () => {
    const message = baseMessage({
      timelineItems: [{
        kind: 'subagent',
        id: 'subagent-call-no-summary',
        toolCallId: 'call-no-summary',
        subagentName: 'researcher',
        status: 'success',
        startedAt: MS_EPOCH,
        finishedAt: MS_EPOCH + 100,
        durationMs: 100,
        resultPreview: 'Final answer from the researcher',
        error: null,
        entries: [],
      }],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));
    expect(html).not.toContain('subagent-card__task-summary');
  });

  it('renders a successful nested tool as a closed disclosure with payload details', () => {
    const rawArgs = '{"path":"src/main.py","include_line_numbers":true}';
    const rawResult = '{"content":"function main()"}';
    const message = baseMessage({
      timelineItems: [{
        kind: 'subagent',
        id: 'subagent-call-nested-tool',
        toolCallId: 'call-nested-tool',
        subagentName: 'researcher',
        status: 'success',
        startedAt: MS_EPOCH,
        finishedAt: MS_EPOCH + 1250,
        durationMs: 1250,
        resultPreview: 'Final answer from the researcher',
        error: null,
        entries: [{
          kind: 'tool',
          id: 'nested-tool-1',
          toolName: 'read_file',
          status: 'success',
          argsPreview: rawArgs,
          resultPreview: rawResult,
          error: null,
          durationMs: 1250,
        }],
      }],
    });
    const html = renderToStaticMarkup(React.createElement(Message, { message }));
    const disclosures = html.match(/<details class="tool-event-disclosure">[\s\S]*?<\/details>/g) || [];
    expect(disclosures).toHaveLength(1);
    const disclosure = disclosures[0] || '';

    expect(disclosure).toContain('read_file · path: src/main.py');
    expect(disclosure).not.toContain('completed');
    expect(disclosure).toContain('>Expand</span>');
    expect(disclosure).toContain('>Collapse</span>');
    expect(disclosure).toContain('class="tool-event-disclosure__chevron"');
    expect(disclosure).not.toMatch(/<details[^>]*\bopen(?:=|\s|>)/);

    const payloadStart = disclosure.indexOf('class="tool chat-event-tool"');
    expect(payloadStart).toBeGreaterThan(-1);
    const summary = disclosure.slice(0, payloadStart);
    const payload = disclosure.slice(payloadStart);
    expect(summary).not.toContain(rawResult.replaceAll('"', '&quot;'));
    expect(payload).toContain(rawArgs.replaceAll('"', '&quot;'));
    expect(payload).toContain(rawResult.replaceAll('"', '&quot;'));
  });

  it('renders reasoning-token metadata only on its reasoning timeline event', () => {
    const message = baseMessage({
      timelineItems: [
        {
          kind: 'stream_event',
          id: 'reasoning-1',
          event: {
            id: 'reasoning-1',
            eventType: 'reasoning_delta',
            timestamp: MS_EPOCH,
            content: 'Checking the result before responding',
            metadata: { reasoning_tokens: 3 },
          },
        },
        {
          kind: 'assistant_message',
          id: 'a1',
          content: 'Response from agent',
          timestamp: MS_EPOCH,
          displayAgentName: 'CodeReviewer',
          displayModelName: 'gpt-4',
          displayToolCalls: ['review'],
          tokenCount: 5,
          reasoningTokenCount: 3,
          reasoningText: '',
          isStreaming: false,
          error: false,
        },
      ],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));
    const reasoningRowStart = html.indexOf('<article class="message-stream-event');
    const assistantRowStart = html.indexOf('<article class="message-wrapper assistant');
    const reasoningRow = html.slice(reasoningRowStart, assistantRowStart);
    const assistantRow = html.slice(assistantRowStart);

    expect(reasoningRowStart).toBeGreaterThan(-1);
    expect(assistantRowStart).toBeGreaterThan(reasoningRowStart);
    expect(reasoningRow).toContain('3 reasoning tokens');
    expect(assistantRow).not.toContain('3 reasoning tokens');
    expect(assistantRow).toContain('5 tokens');
    expect(assistantRow).toContain('CodeReviewer');
    expect(assistantRow).toContain('gpt-4');
    expect(assistantRow).toContain('review');
    expect(assistantRow).toContain('Response from agent');
  });

  it('renders stream_event kind as a stream event row', () => {
    const message = baseMessage({
      timelineItems: [
        {
          kind: 'stream_event',
          id: 'e1',
          event: {
            id: 'e1',
            eventType: 'tool_start',
            content: '',
            metadata: { tool_name: 'search' },
          },
        },
      ],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));

    expect(html).toContain('message-stream-event');
    expect(html).toContain('chat-event-entry');
    expect(html).toContain('entry__badge--tool');
    expect(html).toContain('→ Search');
  });

  it('renders node_update stream event with step tag', () => {
    const message = baseMessage({
      timelineItems: [
        {
          kind: 'stream_event',
          id: 'e1',
          event: {
            id: 'e1',
            eventType: 'node_update',
            content: '',
            metadata: { node_name: 'model_call', output_keys: ['response'] },
          },
        },
      ],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));

    expect(html).toContain('chat-event-tag--step');
    expect(html).toContain('Step');
    expect(html).toContain('Model Call');
    expect(html).toContain('outputs: response');
  });

  it('renders reasoning stream event with reasoning badge', () => {
    const message = baseMessage({
      timelineItems: [
        {
          kind: 'stream_event',
          id: 'e1',
          event: {
            id: 'e1',
            eventType: 'reasoning_delta',
            content: 'Thinking step by step',
            metadata: {},
          },
        },
      ],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));

    expect(html).toContain('chat-event-badge--reason');
    expect(html).toContain('chat-event-tag--reason');
    expect(html).toContain('Reasoning');
  });

  it('reconstructs earlier reasoning ahead of a later tool timeline item with visible text', () => {
    const reasoningTimestamp = '2026-07-15T02:31:08.000Z';
    const toolTimestamp = new Date('2026-07-15T02:31:09.000Z').getTime();
    const message = baseMessage({
      reasoningText: 'Thinking before the tool call',
      reasoningTimestamp,
      timelineItems: [
        {
          kind: 'stream_event',
          id: 'tool-later',
          event: {
            id: 'tool-later',
            eventType: 'tool_start',
            timestamp: toolTimestamp,
            content: '',
            metadata: { tool_name: 'search_web' },
          },
        },
        {
          kind: 'assistant_message',
          id: 'assistant-after-tool',
          content: 'The tool result is ready.',
          timestamp: toolTimestamp + 1000,
          displayAgentName: '',
          displayModelName: '',
          displayToolCalls: [],
          tokenCount: 0,
          reasoningTokenCount: 0,
          reasoningText: '',
          isStreaming: false,
          error: false,
        },
      ],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));
    const reasoningRowPos = html.indexOf('reasoning_summary');
    const toolRowPos = html.indexOf('tool_start');
    const formattedReasoningTimestamp = new Date(reasoningTimestamp).toLocaleTimeString([], {
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    });

    expect(reasoningRowPos).toBeGreaterThan(-1);
    expect(toolRowPos).toBeGreaterThan(reasoningRowPos);
    expect(html.slice(reasoningRowPos, toolRowPos)).toContain('Thinking before the tool call');
    expect(html.slice(reasoningRowPos, toolRowPos)).toContain(formattedReasoningTimestamp);
  });


  it('renders tool_error stream event with error styling', () => {
    const message = baseMessage({
      timelineItems: [
        {
          kind: 'stream_event',
          id: 'e1',
          event: {
            id: 'e1',
            eventType: 'tool_error',
            content: '',
            metadata: { tool_name: 'deploy', error: 'permission denied', duration_ms: 500 },
          },
        },
      ],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));

    expect(html).toContain('entry__badge--err');
    expect(html).toContain('entry__tag--err');
    expect(html).toContain('✗ Deploy');
    expect(html).toContain('permission denied');
  });

  it('defaults displayAgentName to Progenitor when empty', () => {
    const message = baseMessage({
      timelineItems: [
        {
          kind: 'assistant_message',
          id: 'a1',
          content: 'Hi',
          timestamp: MS_EPOCH,
          displayAgentName: '',
          displayModelName: '',
          displayToolCalls: [],
          tokenCount: 0,
          reasoningTokenCount: 0,
          reasoningText: '',
          isStreaming: false,
          error: false,
        },
      ],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));

    expect(html).toContain('Progenitor');
  });
});

/* ------------------------------------------------------------------ */
/*  Fallback: no timelineItems                                          */
/* ------------------------------------------------------------------ */

describe('Message fallback layout without timelineItems', () => {
  it('renders classic layout for assistant message with no timelineItems', () => {
    const message = baseMessage({ timelineItems: undefined });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));

    expect(html).toContain('msg--assistant');
    expect(html).toContain('Hello world');
    expect(html).toContain('entry__tag--assistant');
    expect(html).toContain('chat-entry-chain--single');
    expect(html).not.toContain('chat-entry-chain--connected');
  });

  it('renders stream events above message in classic fallback when streamEvents exist', () => {
    const message = baseMessage({
      timelineItems: undefined,
      streamEvents: [
        {
          id: 'e1',
          eventType: 'tool_start',
          content: '',
          metadata: { tool_name: 'search' },
        },
      ],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));

    expect(html).toContain('chat-entry-chain--connected');
    expect(html).toContain('message-stream-event');
    expect(html).toContain('Hello world');
  });

  it('handles null timelineItems as classic fallback', () => {
    const message = baseMessage({ timelineItems: null });
    const html = renderToStaticMarkup(React.createElement(Message, { message }));
    expect(html).toContain('msg--assistant');
    expect(html).toContain('Hello world');
  });

  it('handles empty timelineItems array as classic fallback', () => {
    const message = baseMessage({ timelineItems: [] });
    const html = renderToStaticMarkup(React.createElement(Message, { message }));
    expect(html).toContain('msg--assistant');
    expect(html).toContain('Hello world');
  });
});

/* ------------------------------------------------------------------ */
/*  User messages                                                       */
/* ------------------------------------------------------------------ */

describe('Message user messages', () => {
  it('are unaffected by timelineItems', () => {
    const message = baseMessage({
      role: 'user',
      content: 'User text here',
      timelineItems: [
        {
          kind: 'assistant_message',
          id: 'a1',
          content: 'Should not render',
          timestamp: MS_EPOCH,
        },
      ],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));

    expect(html).toContain('User text here');
    expect(html).not.toContain('Should not render');
    expect(html).toContain('entry__badge--user');
    expect(html).not.toContain('chat-entry-chain');
  });

  it('render user entry tag and label', () => {
    const message = baseMessage({
      role: 'user',
      content: 'hi',
      timestamp: MS_EPOCH,
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));

    expect(html).toContain('entry__tag--in');
    expect(html).toContain('Message');
  });
});

describe('normal tool content and metadata display', () => {
  it('renders a tool_start summary in normal content with a closed Payload disclosure', () => {
    const rawArgs = '{"path":"src/main.py","include_line_numbers":true}';
    const message = baseMessage({
      timelineItems: [{
        kind: 'stream_event',
        id: 'tool-start-1',
        event: {
          id: 'tool-start-1',
          eventType: 'tool_start',
          timestamp: MS_EPOCH,
          metadata: {
            tool_name: 'read_file',
            args_preview: rawArgs,
          },
        },
      }],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));
    const headerStart = html.indexOf('message-stream-event-header');
    const headerEnd = html.indexOf('</div>', headerStart);
    const header = html.slice(headerStart, headerEnd);
    const disclosureStart = html.indexOf('<details class="tool-event-disclosure">');
    expect(disclosureStart).toBeGreaterThan(-1);
    const disclosure = html.slice(disclosureStart);
    const summaryStart = html.indexOf('class="tool__summary');

    expect(summaryStart).toBeGreaterThan(disclosureStart);
    expect(html).toContain('path: src/main.py');
    expect(header).not.toContain('path: src/main.py');
    expect(disclosure).toContain('tool-event-disclosure__summary');
    expect(disclosure).toContain('>Expand</span>');
    expect(disclosure).toContain('>Collapse</span>');
    expect(disclosure).toContain('class="tool-event-disclosure__chevron"');
    expect(disclosure).not.toMatch(/<details[^>]*\bopen(?:=|\s|>)/);
    const payloadStart = disclosure.indexOf('class="tool chat-event-tool"');
    expect(payloadStart).toBeGreaterThan(-1);
    expect(disclosure.indexOf('class="tool__summary')).toBeLessThan(payloadStart);
    expect(disclosure.indexOf(rawArgs.replaceAll('"', '&quot;'))).toBeGreaterThan(payloadStart);
    expect(disclosure).toContain(rawArgs.replaceAll('"', '&quot;'));
  });

  it('renders call_agent outer start and result rows as closed readable disclosures', () => {
    const rawArgs = '{"agent":"greetings_agent","request":"hi"}';
    const rawResult = 'Agent finished successfully';
    const message = baseMessage({
      timelineItems: [
        {
          kind: 'stream_event',
          id: 'call-agent-start-1',
          event: {
            id: 'call-agent-start-1',
            eventType: 'tool_start',
            timestamp: MS_EPOCH,
            metadata: {
              tool_name: 'call_agent',
              args_preview: rawArgs,
            },
          },
        },
        {
          kind: 'stream_event',
          id: 'call-agent-result-1',
          event: {
            id: 'call-agent-result-1',
            eventType: 'tool_result',
            timestamp: MS_EPOCH,
            metadata: {
              tool_name: 'call_agent',
              duration_ms: 187,
              result_preview: rawResult,
            },
          },
        },
      ],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));
    const disclosures = html.match(/<details class="tool-event-disclosure">[\s\S]*?<\/details>/g) || [];

    expect(disclosures).toHaveLength(2);
    expect(disclosures.every((disclosure) => !/<details[^>]*\bopen(?:=|\s|>)/.test(disclosure))).toBe(true);
    expect(disclosures[0]).toContain('agent: greetings_agent • request: hi');
    expect(disclosures[0]).toContain(rawArgs.replaceAll('"', '&quot;'));
    expect(disclosures[1]).toContain('Agent finished successfully');
    expect(html).toContain('187ms');
    expect(disclosures[1]).toContain(rawResult);
    expect(disclosures.every((disclosure) => disclosure.includes('class="tool chat-event-tool"'))).toBe(true);
  });

  it('renders artifact result summary as content, raw JSON inside closed Payload, and duration before timestamp', () => {
    const rawResult = '{"content":"raw result"}';
    const message = baseMessage({
      timelineItems: [{
        kind: 'stream_event',
        id: 'tool-result-1',
        event: {
          id: 'tool-result-1',
          eventType: 'tool_result',
          timestamp: MS_EPOCH,
          metadata: {
            tool_name: 'read_file',
            duration_ms: 1250,
            artifact: { summary: '12 lines read' },
            result_preview: rawResult,
          },
        },
      }],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));
    const timestamp = new Date(MS_EPOCH).toLocaleTimeString([], {
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    });
    const headerStart = html.indexOf('message-stream-event-header');
    const headerEnd = html.indexOf('</div>', headerStart);
    const header = html.slice(headerStart, headerEnd);
    const disclosureStart = html.indexOf('<details class="tool-event-disclosure">');
    const disclosure = html.slice(disclosureStart);

    expect(html).toContain('12 lines read');
    expect(header).toContain('1.25s');
    expect(header).not.toContain('12 lines read');
    expect(header).not.toContain(rawResult);
    expect(header.indexOf('1.25s')).toBeLessThan(header.indexOf(timestamp));
    expect(disclosure).toContain('tool-event-disclosure__summary');
    expect(disclosure).toContain('>Expand</span>');
    expect(disclosure).toContain('>Collapse</span>');
    expect(disclosure).toContain('class="tool-event-disclosure__chevron"');
    expect(disclosure).not.toMatch(/<details[^>]*\bopen(?:=|\s|>)/);
    const payloadStart = disclosure.indexOf('class="tool chat-event-tool"');
    expect(payloadStart).toBeGreaterThan(-1);
    expect(disclosure.indexOf(rawResult.replaceAll('"', '&quot;'))).toBeGreaterThan(payloadStart);
    expect(disclosure).toContain(rawResult.replaceAll('"', '&quot;'));
  });

  it('keeps the fallback stream-event renderer consistent with TimelineItem', () => {
    const rawArgs = '{"query":"status"}';
    const message = baseMessage({
      timelineItems: undefined,
      streamEvents: [{
        id: 'fallback-tool-start-1',
        eventType: 'tool_start',
        metadata: { tool_name: 'search_web', args_preview: rawArgs },
      }],
    });
    const html = renderToStaticMarkup(React.createElement(Message, { message }));

    const disclosureStart = html.indexOf('<details class="tool-event-disclosure">');
    expect(disclosureStart).toBeGreaterThan(-1);
    const disclosure = html.slice(disclosureStart);
    expect(html).toContain('class="tool__summary');
    expect(html).toContain('query: status');
    expect(disclosure).toContain('tool-event-disclosure__summary');
    expect(disclosure).toContain('>Expand</span>');
    expect(disclosure).toContain('>Collapse</span>');
    expect(disclosure).toContain('class="tool-event-disclosure__chevron"');
    const payloadStart = disclosure.indexOf('class="tool chat-event-tool"');
    expect(payloadStart).toBeGreaterThan(-1);
    expect(disclosure.indexOf(rawArgs.replaceAll('"', '&quot;'))).toBeGreaterThan(payloadStart);
    expect(disclosure).toContain(rawArgs.replaceAll('"', '&quot;'));
  });

  it('renders legacy call_agent stream events as closed readable disclosures', () => {
    const rawArgs = '{"agent":"greetings_agent","request":"hi"}';
    const rawResult = 'Legacy route completed';
    const message = baseMessage({
      timelineItems: undefined,
      streamEvents: [
        {
          id: 'legacy-call-agent-start-1',
          eventType: 'tool_start',
          metadata: {
            tool_name: 'call_agent',
            args_preview: rawArgs,
          },
        },
        {
          id: 'legacy-call-agent-result-1',
          eventType: 'tool_result',
          metadata: {
            tool_name: 'call_agent',
            duration_ms: 91,
            result_preview: rawResult,
          },
        },
      ],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));
    const disclosures = html.match(/<details class="tool-event-disclosure">[\s\S]*?<\/details>/g) || [];

    expect(disclosures).toHaveLength(2);
    expect(disclosures.every((disclosure) => !/<details[^>]*\bopen(?:=|\s|>)/.test(disclosure))).toBe(true);
    expect(disclosures[0]).toContain('agent: greetings_agent • request: hi');
    expect(disclosures[0]).toContain(rawArgs.replaceAll('"', '&quot;'));
    expect(disclosures[1]).toContain('Legacy route completed');
    expect(html).toContain('91ms');
  });

  it('keeps fallback result summaries out of the header while retaining raw output', () => {
    const rawResult = '{"content":"fallback raw result"}';
    const message = baseMessage({
      timelineItems: undefined,
      streamEvents: [{
        id: 'fallback-tool-result-1',
        eventType: 'tool_result',
        timestamp: MS_EPOCH,
        metadata: {
          tool_name: 'read_file',
          duration_ms: 750,
          artifact: { summary: '7 lines read' },
          result_preview: rawResult,
        },
      }],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));
    const headerStart = html.indexOf('message-stream-event-header');
    const headerEnd = html.indexOf('</div>', headerStart);
    const header = html.slice(headerStart, headerEnd);
    const disclosureStart = html.indexOf('<details class="tool-event-disclosure">');
    const disclosure = html.slice(disclosureStart);

    expect(header).toContain('750ms');
    expect(header).not.toContain('7 lines read');
    expect(header).not.toContain(rawResult);
    expect(html).toContain('class="tool__summary');
    expect(html).toContain('7 lines read');
    expect(disclosure).toContain('tool-event-disclosure__summary');
    expect(disclosure).toContain('>Expand</span>');
    expect(disclosure).toContain('>Collapse</span>');
    const payloadStart = disclosure.indexOf('class="tool chat-event-tool"');
    expect(payloadStart).toBeGreaterThan(-1);
    expect(disclosure.indexOf(rawResult.replaceAll('"', '&quot;'))).toBeGreaterThan(payloadStart);
    expect(disclosure).toContain('class="tool-event-disclosure__chevron"');
    expect(disclosure).toContain(rawResult.replaceAll('"', '&quot;'));
  });
  it('renders a concise list_agents summary while retaining structured raw output', () => {
    const rawResult = JSON.stringify({ agents: [{ name: 'alpha' }, { name: 'beta' }] });
    const message = baseMessage({
      timelineItems: [{
        kind: 'stream_event',
        id: 'list-agents-result-1',
        event: {
          id: 'list-agents-result-1',
          eventType: 'tool_result',
          timestamp: MS_EPOCH,
          metadata: {
            tool_name: 'list_agents',
            duration_ms: 240,
            result_preview: rawResult,
          },
        },
      }],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));
    const headerStart = html.indexOf('message-stream-event-header');
    const headerEnd = html.indexOf('</div>', headerStart);
    const header = html.slice(headerStart, headerEnd);
    const disclosureStart = html.indexOf('<details class="tool-event-disclosure">');
    expect(disclosureStart).toBeGreaterThan(-1);
    const disclosure = html.slice(disclosureStart);
    const payloadStart = disclosure.indexOf('class="tool chat-event-tool"');
    const escapedResult = rawResult.replaceAll('"', '&quot;');

    expect(html).toContain('Found 2 agents');
    expect(header).not.toContain('Found 2 agents');
    expect(header).not.toContain(rawResult);
    expect(disclosure).not.toMatch(/<details[^>]*\bopen(?:=|\s|>)/);
    expect(disclosure).toContain('tool-event-disclosure__summary');
    expect(disclosure).toContain('class="tool__summary tool-event-disclosure__summary">Found 2 agents</span>');
    expect(disclosure).toContain('>Expand</span>');
    expect(payloadStart).toBeGreaterThan(-1);
    expect(disclosure.indexOf('class="tool__summary')).toBeLessThan(payloadStart);
    expect(disclosure.indexOf(escapedResult)).toBeGreaterThan(payloadStart);
    expect(disclosure).toContain(escapedResult);
    expect(disclosure).not.toContain(rawResult);
  });
});

describe('reasoning summary presentation', () => {
  it('renders explicit summary and detail metadata in normal content with an exact closed disclosure count', () => {
    const providerSummary = 'The provider supplied this summary.';
    const providerDetail = 'Evidence 🔍';
    const unstructuredContent = 'This event content must never be rendered.';
    const message = baseMessage({
      timelineItems: [{
        kind: 'stream_event',
        id: 'provider-reasoning-summary',
        event: {
          id: 'provider-reasoning-summary',
          eventType: 'reasoning_summary',
          content: unstructuredContent,
          metadata: { summary: providerSummary, detail: providerDetail },
        },
      }],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));
    const disclosure = html.match(/<details\b[^>]*\breasoning-event-disclosure\b[^>]*>[\s\S]*?<\/details>/)?.[0] || '';
    const summaryEnd = disclosure.indexOf('</summary>');
    const summary = disclosure.slice(0, summaryEnd);
    const detail = disclosure.slice(summaryEnd);

    expect(disclosure).not.toMatch(/<details[^>]*\bopen(?:=|\s|>)/);
    expect(summary).toMatch(/class="[^"]*\bmessage-content\b[^"]*\breasoning-event-disclosure__summary/);
    expect(summary).toContain(providerSummary);
    expect(summary).not.toContain(providerDetail);
    expect(summary).not.toContain(unstructuredContent);
    expect(detail).toContain(providerDetail);
    expect(detail).not.toContain(unstructuredContent);
  });


  it('renders reconstructed persisted reasoning as a visible summary and closed paragraph detail', () => {
    const summaryParagraph = 'The persisted agent checked the repository state.';
    const detailParagraph = 'It compared the changed files with the requested behavior.';
    const finalParagraph = 'The remaining differences explain the next repair step.';
    const message = baseMessage({
      timelineItems: [{
        kind: 'stream_event',
        id: 'persisted-reasoning',
        event: {
          id: 'persisted-reasoning',
          eventType: 'reasoning_summary',
          content: `${summaryParagraph}\n\n${detailParagraph}\n\n${finalParagraph}`,
          metadata: {
            source: 'message_reasoning',
            text_length: summaryParagraph.length + detailParagraph.length + finalParagraph.length + 4,
          },
        },
      }],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));
    const disclosure = html.match(/<details\b[^>]*\breasoning-event-disclosure\b[^>]*>[\s\S]*?<\/details>/)?.[0] || '';
    const summaryEnd = disclosure.indexOf('</summary>');
    const summary = disclosure.slice(0, summaryEnd);
    const detail = disclosure.slice(summaryEnd);

    expect(summaryEnd).toBeGreaterThan(-1);
    expect(disclosure).not.toMatch(/<details[^>]*\bopen(?:=|\s|>)/);
    expect(summary).toContain(summaryParagraph);
    expect(summary).not.toContain(detailParagraph);
    expect(summary).not.toContain(finalParagraph);
    expect(detail).toContain(detailParagraph);
    expect(detail).toContain(finalParagraph);
    expect(disclosure).toContain('>Expand</span>');
    expect(disclosure).toContain('>Collapse</span>');
  });

  it('renders a metadata summary alone in normal message-content markup without a disclosure', () => {
    const providerSummary = 'The provider supplied the complete summary.';
    const unstructuredContent = 'Event content does not substitute for detail.';
    const message = baseMessage({
      timelineItems: [{
        kind: 'stream_event',
        id: 'summary-only-reasoning',
        event: {
          id: 'summary-only-reasoning',
          eventType: 'reasoning_summary',
          content: unstructuredContent,
          metadata: { summary: providerSummary },
        },
      }],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));

    expect(html).toContain('class="message-content reasoning-event-content entry__content"');
    expect(html).toContain(providerSummary);
    expect(html).not.toContain(unstructuredContent);
    expect(html).not.toContain('reasoning-event-disclosure');
  });
  it('renders explicit reasoning summary and detail metadata in the classic fallback stream', () => {
    const providerSummary = 'The provider supplied this summary.';
    const providerDetail = 'The fallback preserved the detailed trace.';
    const unstructuredContent = 'This event content must never be rendered.';
    const message = baseMessage({
      timelineItems: undefined,
      streamEvents: [{
        id: 'fallback-reasoning-summary',
        eventType: 'reasoning_summary',
        content: unstructuredContent,
        metadata: { summary: providerSummary, detail: providerDetail },
      }],
    });

    const html = renderToStaticMarkup(React.createElement(Message, { message }));
    const eventStart = html.indexOf('message-stream-event');
    const assistantStart = html.indexOf('Hello world');
    const summaryPos = html.indexOf(providerSummary);
    const detailPos = html.indexOf(providerDetail);

    expect(eventStart).toBeGreaterThan(-1);
    expect(summaryPos).toBeGreaterThan(eventStart);
    expect(detailPos).toBeGreaterThan(summaryPos);
    expect(detailPos).toBeLessThan(assistantStart);
    expect(html).not.toContain(unstructuredContent);
  });
});
