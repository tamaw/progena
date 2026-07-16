// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const markdownRenderer = vi.hoisted(() => ({
  renderMarkdownToHtml: vi.fn((content) => (
    `<pre><code class="language-js">${content}</code></pre><img src="x" onerror="alert(1)">`
  )),
}));

vi.mock('../../lib/markdown', () => ({
  renderMarkdownToHtml: markdownRenderer.renderMarkdownToHtml,
}));

import {
  appendStreamingCursor,
  describeStreamEvent,
  toolDetailsForEvent,
  extractCallAgentSummary,
  formatTimestamp,
  renderSafeMarkdown,
} from './messageRenderUtils';

function todosEvent(todos) {
  return { eventType: 'todos_update', metadata: { todos } };
}


describe('renderSafeMarkdown terminal cache', () => {
  beforeEach(() => {
    markdownRenderer.renderMarkdownToHtml.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('reuses the sanitized fenced-code result across a terminal remount and invalidates when the source changes', () => {
    const terminalFence = '```js\nconst answer = 42;\n```';
    const changedFence = '```js\nconst answer = 43;\n```';
    const createElement = vi.spyOn(document, 'createElement');

    const initialHtml = renderSafeMarkdown(terminalFence);
    const remountedHtml = renderSafeMarkdown(terminalFence);
    const changedHtml = renderSafeMarkdown(changedFence);

    expect(initialHtml).toBe(remountedHtml);
    expect(changedHtml).not.toBe(initialHtml);
    expect(initialHtml).toContain('<pre><code class="language-js">');
    expect(initialHtml).not.toContain('<img');
    expect(markdownRenderer.renderMarkdownToHtml).toHaveBeenCalledTimes(2);
    expect(markdownRenderer.renderMarkdownToHtml).toHaveBeenNthCalledWith(1, terminalFence);
    expect(markdownRenderer.renderMarkdownToHtml).toHaveBeenNthCalledWith(2, changedFence);
    expect(createElement.mock.calls.filter(([tagName]) => tagName === 'template')).toHaveLength(2);

});
});
describe('describeStreamEvent (todos_update)', () => {
  it('titles the entry after the in_progress todo when one exists', () => {
    const description = describeStreamEvent(todosEvent([
      { content: 'Read the config', status: 'completed' },
      { content: 'Patch the handler', status: 'in_progress' },
      { content: 'Run the tests', status: 'pending' },
    ]));
    expect(description.badge).toBe('Plan');
    expect(description.title).toBe('Patch the handler');
    expect(description.metaDetail).toBe('1/3 done');
  });

  it('falls back to the first pending todo when nothing is in_progress', () => {
    const description = describeStreamEvent(todosEvent([
      { content: 'Read the config', status: 'completed' },
      { content: 'Run the tests', status: 'pending' },
    ]));
    expect(description.title).toBe('Run the tests');
  });

  it('falls back to the last todo once everything is completed', () => {
    const description = describeStreamEvent(todosEvent([
      { content: 'Read the config', status: 'completed' },
      { content: 'Run the tests', status: 'completed' },
    ]));
    expect(description.title).toBe('Run the tests');
  });

  it('falls back to the generic "Plan" label when there are no todos', () => {
    const description = describeStreamEvent(todosEvent([]));
    expect(description.title).toBe('Plan');
    expect(description.metaDetail).toBe('');
  });
});

// These six event types only reach describeStreamEvent via the generic
// stream_event fallback renderer -- e.g. chats persisted before subagent
// cards were grouped into their own timeline item kind (see
// buildDefaultAssistantTimeline / applySubagentEvent in
// liveAssistantTurnModel.js). Before this fix, the unexported
// badgeForEventType() helper's `.includes('agent')` check accidentally
// matched every "subagent_*" type (since "subagent" contains "agent") and
// mislabeled them "Agent".
describe('describeStreamEvent (subagent_*)', () => {
  it('never returns the accidental "Agent" badge for any subagent_* event type', () => {
    const eventTypes = [
      'subagent_start',
      'subagent_result',
      'subagent_message_delta',
      'subagent_tool_start',
      'subagent_tool_result',
      'subagent_tool_error',
    ];
    for (const eventType of eventTypes) {
      const description = describeStreamEvent({ eventType, metadata: { subagent_name: 'researcher' } });
      expect(description.badge).toBe('Subagent');
    }
  });

  it('subagent_start titles the entry with the subagent name', () => {
    const description = describeStreamEvent({
      eventType: 'subagent_start',
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1' },
    });
    expect(description.title).toBe('→ researcher');
  });

  it('subagent_result reports success with duration and result preview', () => {
    const description = describeStreamEvent({
      eventType: 'subagent_result',
      metadata: {
        subagent_name: 'researcher',
        tool_call_id: 'call_1',
        success: true,
        duration_ms: 1500,
        result_preview: 'Found 3 matches',
      },
    });
    expect(description.title).toBe('✓ researcher');
    expect(description.metaDetail).toBe('1.50s • Found 3 matches');
  });

  it('subagent_result reports failure with the error message', () => {
    const description = describeStreamEvent({
      eventType: 'subagent_result',
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1', success: false, error: 'boom' },
    });
    expect(description.title).toBe('✗ researcher');
    expect(description.metaDetail).toBe('boom');
  });

  // bodyHtml for non-empty content goes through formatMessage() (markdown ->
  // HTML via a real DOM), which this suite's node test environment doesn't
  // provide -- same scope limitation as the untested reasoning bodyHtml
  // branch elsewhere in describeStreamEvent. Covered instead by the
  // SubagentCard manual verification.
  it('subagent_message_delta titles the entry with the subagent name and skips formatMessage for empty content', () => {
    const description = describeStreamEvent({
      eventType: 'subagent_message_delta',
      content: '',
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1' },
    });
    expect(description.title).toBe('researcher');
    expect(description.bodyHtml).toBe('');
  });

  it('subagent_tool_start/result/error use the nested tool name, not the parent task', () => {
    const start = describeStreamEvent({
      eventType: 'subagent_tool_start',
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1', nested_tool_name: 'read_file' },
    });
    expect(start.title).toBe('researcher → Read File');

    const result = describeStreamEvent({
      eventType: 'subagent_tool_result',
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1', nested_tool_name: 'read_file', duration_ms: 5 },
    });
    expect(result.title).toBe('researcher ✓ Read File');

    const error = describeStreamEvent({
      eventType: 'subagent_tool_error',
      metadata: { subagent_name: 'researcher', tool_call_id: 'call_1', nested_tool_name: 'read_file', error: 'not found' },
    });
    expect(error.title).toBe('researcher ✗ Read File');
    expect(error.metaDetail).toBe('not found');
  });

  it('toolDetailsForEvent returns null for subagent_tool_* despite containing "tool" in the type', () => {
    expect(toolDetailsForEvent(
      { eventType: 'subagent_tool_start', metadata: { nested_tool_name: 'read_file' } },
      describeStreamEvent({ eventType: 'subagent_tool_start', metadata: { nested_tool_name: 'read_file' } }),
    )).toBeNull();
  });
});

// a2a_call_progress only reaches describeStreamEvent via the generic
// stream_event fallback renderer -- e.g. chats persisted before A2A call
// grouping shipped, or a progress event that arrives before its tool_start
// (buildDefaultAssistantTimeline groups them into a single A2ACallCard
// otherwise -- see liveAssistantTurnModel.js). Same "Agent" mislabel bug as
// subagent_* would apply here too (badgeForEventType().includes('agent')),
// so it's handled with an explicit badge: 'A2A' branch the same way.
describe('describeStreamEvent (a2a_call_progress)', () => {
  it('never returns the accidental "Agent" badge', () => {
    const description = describeStreamEvent({
      eventType: 'a2a_call_progress',
      metadata: { agent_name: 'greetings_agent', state: 'working', message_text: 'Step 1/3: prep...' },
    });
    expect(description.badge).toBe('A2A');
  });

  it('titles the entry with the agent name and surfaces state/message', () => {
    const description = describeStreamEvent({
      eventType: 'a2a_call_progress',
      metadata: { agent_name: 'greetings_agent', state: 'working', message_text: 'Step 1/3: prep...' },
    });
    expect(description.title).toBe('greetings_agent');
    expect(description.metaDetail).toBe('working');
    expect(description.bodyDetail).toBe('Step 1/3: prep...');
  });

  it('falls back to a generic agent label when agent_name is missing', () => {
    const description = describeStreamEvent({
      eventType: 'a2a_call_progress',
      metadata: { state: 'working', message_text: 'hi' },
    });
    expect(description.title).toBe('agent');
  });
});

// Progenitor's own dispatch/return for call_agent is an ordinary tool call
// and gets no special-casing here -- badged 'Tool', titled with its own name
// (prettifyLabel('call_agent')), exactly like search_web or read_file would
// be. Only the intermediate a2a_call_progress (tested above) is genuinely
// new information with no tool-call equivalent, so it alone gets its own
// 'A2A' badge.
describe('describeStreamEvent (call_agent tool_start/tool_result/tool_error)', () => {
  it('badges a call_agent tool_start as Tool, same as any other tool', () => {
    const description = describeStreamEvent({
      eventType: 'tool_start',
      metadata: { tool_name: 'call_agent', args_preview: { agent: 'greetings_agent' } },
    });
    expect(description.badge).toBe('Tool');
    expect(description.title).toBe('→ Call Agent');
  });

  it('badges a call_agent tool_result as Tool, same as any other tool', () => {
    const description = describeStreamEvent({
      eventType: 'tool_result',
      metadata: { tool_name: 'call_agent', result_preview: 'Hello!' },
    });
    expect(description.badge).toBe('Tool');
    expect(description.title).toBe('✓ Call Agent');
  });

  it('badges a call_agent tool_error as Tool, same as any other tool', () => {
    const description = describeStreamEvent({
      eventType: 'tool_error',
      metadata: { tool_name: 'call_agent', error: 'agent unreachable' },
    });
    expect(description.badge).toBe('Tool');
    expect(description.title).toBe('✗ Call Agent');
  });
});

describe('extractCallAgentSummary', () => {
  it('returns empty string for falsy input', () => {
    expect(extractCallAgentSummary('')).toBe('');
    expect(extractCallAgentSummary(null)).toBe('');
    expect(extractCallAgentSummary(undefined)).toBe('');
  });

  it('extracts summary from complete, valid JSON', () => {
    const json = JSON.stringify({ task_id: 't1', agent: 'greetings_agent', summary: 'Hello there.' });
    expect(extractCallAgentSummary(json)).toBe('Hello there.');
  });

  it('returns empty string when valid JSON has no summary field', () => {
    const json = JSON.stringify({ task_id: 't1', agent: 'greetings_agent' });
    expect(extractCallAgentSummary(json)).toBe('');
  });

  it('pulls summary via regex when the JSON is truncated after it', () => {
    const truncated = '{"task_id": "t1", "summary": "Hello there.", "task": {"history": [{"kind": "mess…';
    expect(extractCallAgentSummary(truncated)).toBe('Hello there.');
  });

  it('unescapes JSON string escapes (newlines, quotes) in the extracted summary', () => {
    const truncated = String.raw`{"summary": "Line one\nLine \"two\"", "task": {"h…`;
    expect(extractCallAgentSummary(truncated)).toBe('Line one\nLine "two"');
  });

  it('returns empty string when the summary value itself was truncated (no closing quote)', () => {
    const truncated = '{"task_id": "t1", "summary": "Hello the…';
    expect(extractCallAgentSummary(truncated)).toBe('');
  });

  it('returns empty string for non-JSON garbage with no summary key', () => {
    expect(extractCallAgentSummary('not json at all')).toBe('');
  });
});

describe('normal tool detail rendering', () => {
  it('separates a tool_start human summary from raw args and duration', () => {
    const event = {
      eventType: 'tool_start',
      metadata: {
        tool_name: 'read_file',
        args_preview: JSON.stringify({ path: 'src/main.py', include_line_numbers: true }),
        duration_ms: 12,
      },
    };
    const details = toolDetailsForEvent(event, describeStreamEvent(event));

    expect(details.summary).toBe('path: src/main.py');
    expect(details.duration).toBe('12ms');
    expect(details.command).toBe('{"path":"src/main.py","include_line_numbers":true}');
    expect(details.body).toBe('');
    expect(details.summary).not.toContain(details.command);
  });

  it('treats task outer lifecycle rows as normal tool disclosures', () => {
    const start = {
      eventType: 'tool_start',
      metadata: {
        tool_name: 'task',
        args_preview: { subagent_type: 'researcher', description: 'look into it' },
      },
    };
    const result = {
      eventType: 'tool_result',
      metadata: {
        tool_name: 'task',
        duration_ms: 42,
        result_preview: 'Found it',
      },
    };

    const startDetails = toolDetailsForEvent(start, describeStreamEvent(start));
    const resultDetails = toolDetailsForEvent(result, describeStreamEvent(result));

    expect(startDetails).toMatchObject({
      kind: 'task',
      isNormal: true,
      summary: 'subagent_type: researcher',
      command: '{"subagent_type":"researcher","description":"look into it"}',
      body: '',
    });
    expect(resultDetails).toMatchObject({
      kind: 'task',
      isNormal: true,
      summary: 'Found it',
      duration: '42ms',
      command: '',
      body: 'Found it',
    });
    expect(startDetails.summary).not.toContain(startDetails.command);
  });

  it('treats call_agent outer lifecycle rows as normal tool disclosures', () => {
    const start = {
      eventType: 'tool_start',
      metadata: {
        tool_name: 'call_agent',
        args_preview: { agent: 'greetings_agent', request: 'hi' },
      },
    };
    const result = {
      eventType: 'tool_result',
      metadata: {
        tool_name: 'call_agent',
        duration_ms: 187,
        result_preview: 'Hello! Completed the request.',
      },
    };

    const startDetails = toolDetailsForEvent(start, describeStreamEvent(start));
    const resultDetails = toolDetailsForEvent(result, describeStreamEvent(result));

    expect(startDetails).toMatchObject({
      kind: 'call_agent',
      isNormal: true,
      summary: 'agent: greetings_agent • request: hi',
      command: '{"agent":"greetings_agent","request":"hi"}',
      body: '',
    });
    expect(resultDetails).toMatchObject({
      kind: 'call_agent',
      isNormal: true,
      summary: 'Hello! Completed the request.',
      duration: '187ms',
      command: '',
      body: 'Hello! Completed the request.',
    });
  });

  it('prefers artifact.summary for result summaries while preserving raw output and duration', () => {
    const event = {
      eventType: 'tool_result',
      metadata: {
        tool_name: 'read_file',
        duration_ms: 1250,
        artifact: { summary: '12 lines read' },
        result_preview: '{"content":"raw result"}',
      },
    };
    const details = toolDetailsForEvent(event, describeStreamEvent(event));

    expect(details.summary).toBe('12 lines read');
    expect(details.duration).toBe('1.25s');
    expect(details.command).toBe('');
    expect(details.body).toBe('{"content":"raw result"}');
    expect(details.summary).not.toContain(details.body);
  });

  it('preserves an already-annotated omitted-line summary for a truncated file preview', () => {
    const event = {
      eventType: 'tool_result',
      metadata: {
        tool_name: 'read_file',
        artifact: { kind: 'file', summary: 'src/main.py: 5 lines · 2 more lines not shown', count: 5 },
        result_preview: 'line 1\nline 2\nline 3',
      },
    };
    const details = toolDetailsForEvent(event, describeStreamEvent(event));

    expect(details.summary).toBe('src/main.py: 5 lines · 2 more lines not shown');
  });

  it('keeps a file artifact summary unchanged when all preview lines are present', () => {
    const event = {
      eventType: 'tool_result',
      metadata: {
        tool_name: 'read_file',
        artifact: { kind: 'file', summary: 'src/main.py: 3 lines', count: 3 },
        result_preview: 'line 1\nline 2\nline 3',
      },
    };
    const details = toolDetailsForEvent(event, describeStreamEvent(event));

    expect(details.summary).toBe('src/main.py: 3 lines');
  });

  it('keeps serialized todo results out of the human-readable summary', () => {
    const rawResult = '{"todos":[{"content":"Ship feature","status":"completed"},{"content":"Add tests","status":"in_progress"},{"content":"Deploy","status":"pending"}]}';
    const event = {
      eventType: 'tool_result',
      metadata: {
        tool_name: 'write_todos',
        result_preview: rawResult,
      },
    };
    const details = toolDetailsForEvent(event, describeStreamEvent(event));

    expect(details.summary).toBe('Updated todo list · 3 items');
    expect(details.summary).not.toContain('Ship feature');
    expect(details.command).toBe('');
    expect(details.body).toBe(rawResult);
  });

  it('keeps raw error text separate from the human-readable error summary', () => {
    const event = {
      eventType: 'tool_error',
      metadata: {
        tool_name: 'deploy',
        duration_ms: 125,
        artifact: { summary: 'Deployment denied' },
        error: '{"message":"permission denied","request_id":"r-1"}',
      },
    };
    const details = toolDetailsForEvent(event, describeStreamEvent(event));

    expect(details.summary).toBe('Deployment denied');
    expect(details.duration).toBe('125ms');
    expect(details.command).toBe('');
    expect(details.body).toBe('{"message":"permission denied","request_id":"r-1"}');
  });
});

describe('describeStreamEvent normal tool metadata', () => {
  it('uses duration only for a normal tool_result without reading result_preview', () => {
    const event = {
      eventType: 'tool_result',
      metadata: {
        tool_name: 'read_file',
        duration_ms: 1250,
        get result_preview() {
          throw new Error('result_preview should not be evaluated for normal tools');
        },
      },
    };

    expect(describeStreamEvent(event).metaDetail).toBe('1.25s');
  });

  it('uses duration only for a normal tool_error without reading error', () => {
    const event = {
      eventType: 'tool_error',
      metadata: {
        tool_name: 'read_file',
        duration_ms: 125,
        get error() {
          throw new Error('error should not be evaluated for normal tools');
        },
      },
    };

    expect(describeStreamEvent(event).metaDetail).toBe('125ms');
  });
});

describe('formatTimestamp', () => {
  it('formats an ISO timestamp string as a local time with seconds', () => {
    const isoTimestamp = '2026-07-15T02:31:08.000Z';
    const expected = new Date(isoTimestamp).toLocaleTimeString([], {
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    });

    expect(formatTimestamp(isoTimestamp)).toBe(expected);
  });
});

describe('appendStreamingCursor', () => {
  function parse(html) {
    const container = document.createElement('div');
    container.innerHTML = html;
    return container;
  }

  it('returns one cursor span for empty input', () => {
    const container = parse(appendStreamingCursor(''));

    expect(container.children).toHaveLength(1);
    expect(container.firstElementChild.tagName).toBe('SPAN');
    expect(container.firstElementChild.className).toBe('stream-cursor');
    expect(container.firstElementChild.textContent).toBe('▋');
  });

  it('places the cursor inside a paragraph after its text', () => {
    const container = parse(appendStreamingCursor('<p>Hello</p>'));
    const paragraph = container.querySelector('p');
    const cursor = container.querySelector('.stream-cursor');

    expect(cursor).not.toBeNull();
    expect(cursor.parentElement).toBe(paragraph);
    expect(paragraph.lastElementChild).toBe(cursor);
    expect(paragraph.textContent).toBe('Hello▋');
  });

  it('places the cursor inside the deepest final element of nested markup', () => {
    const container = parse(appendStreamingCursor('<blockquote><p><strong>Final</strong></p></blockquote>'));
    const cursor = container.querySelector('.stream-cursor');

    expect(cursor).not.toBeNull();
    expect(cursor.parentElement?.tagName).toBe('STRONG');
    expect(cursor.parentElement?.parentElement?.tagName).toBe('P');
    expect(cursor.parentElement?.lastElementChild).toBe(cursor);
    expect(container.querySelectorAll('.stream-cursor')).toHaveLength(1);
  });

  it('places the cursor in the final node among multiple top-level nodes', () => {
    const container = parse(appendStreamingCursor('<p>First</p><p>Second</p>'));
    const paragraphs = container.querySelectorAll('p');
    const cursor = container.querySelector('.stream-cursor');

    expect(cursor).not.toBeNull();
    expect(cursor.parentElement).toBe(paragraphs[1]);
    expect(paragraphs[0].querySelector('.stream-cursor')).toBeNull();
    expect(paragraphs[1].textContent).toBe('Second▋');
    expect(container.querySelectorAll('.stream-cursor')).toHaveLength(1);
  });
});
