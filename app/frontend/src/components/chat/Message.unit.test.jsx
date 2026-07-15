import React from 'react';
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { Message } from './Message';

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

  it('renders assistant_message with agent header, displayAgentName, and model details', () => {
    const message = baseMessage({
      timelineItems: [
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

    expect(html).toContain('entry__tag--assistant');
    expect(html).toContain('agent');
    expect(html).toContain('CodeReviewer');
    expect(html).toContain('gpt-4');
    expect(html).toContain('review');
    expect(html).toContain('Response from agent');
    expect(html).toContain('5 tokens');
    expect(html).toContain('3 reasoning tokens');
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
});
