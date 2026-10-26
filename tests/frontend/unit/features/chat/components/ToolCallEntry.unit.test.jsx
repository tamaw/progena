// @vitest-environment jsdom
import React from 'react';
import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import ToolCallEntry from '@frontend/features/chat/components/ToolCallEntry';

if (typeof globalThis.React === 'undefined') {
  globalThis.React = React;
}

function row(id, eventType, metadata) {
  return { kind: 'stream_event', id, event: { id, eventType, content: '', metadata: { tool_name: 'read_file', tool_call_id: 'c', ...metadata } } };
}

describe('ToolCallEntry', () => {
  it('shows narration in full above the call line, which summarises the input and opens to input and output boxes', () => {
    const narration = 'I’ll check the **config** first, then decide whether the saved routine needs another pass before relaunching it.';
    const html = renderToStaticMarkup(React.createElement(ToolCallEntry, {
      item: {
        kind: 'tool_call',
        id: 'call-c',
        start: row('s', 'tool_start', { args_preview: { path: '/config/a.json' }, commentary: narration }),
        end: row('e', 'tool_result', { result_preview: 'line one', duration_ms: 12 }),
      },
    }));

    expect(html.match(/entry__role/g)).toHaveLength(1);
    const narrationPos = html.indexOf('tool-call-entry__narration');
    const linePos = html.indexOf('tool-call-entry__line');
    expect(narrationPos).toBeGreaterThan(-1);
    // Outside the disclosure, so it shows without opening the call.
    expect(narrationPos).toBeLessThan(html.indexOf('<details'));
    expect(html.slice(narrationPos, linePos)).toContain('<strong>config</strong>');
    expect(html.slice(narrationPos, linePos)).toContain('before relaunching it.');
    const summary = html.slice(linePos, html.indexOf('</summary>'));
    expect(summary).toContain('Read File');
    expect(summary).toContain('/config/a.json');
    expect(summary).not.toContain('check the');
    const inputPos = html.indexOf('tool-io__label">Input');
    const outputPos = html.indexOf('tool-io__label">Output');
    expect(outputPos).toBeGreaterThan(inputPos);
    expect(html.slice(inputPos, outputPos)).toContain('/config/a.json');
    expect(html.slice(outputPos)).toContain('line one');
    expect(html).toContain('12ms');
  });

  it('marks a call that has not finished as running and a failed call as an error', () => {
    const running = renderToStaticMarkup(React.createElement(ToolCallEntry, {
      item: { kind: 'tool_call', id: 'call-c', start: row('s', 'tool_start', {}), end: null },
    }));
    expect(running).toContain('tool-call-entry--running');

    const failed = renderToStaticMarkup(React.createElement(ToolCallEntry, {
      item: { kind: 'tool_call', id: 'call-c', start: row('s', 'tool_start', {}), end: row('e', 'tool_error', { error: 'denied' }) },
    }));
    expect(failed).toContain('entry__badge--err');
    expect(failed).toContain('denied');
  });

  it("renders a write_todos call that produced a plan as the plan's current item with progress and its checklist", () => {
    const html = renderToStaticMarkup(React.createElement(ToolCallEntry, {
      item: {
        kind: 'tool_call',
        id: 'call-c',
        start: row('s', 'tool_start', { tool_name: 'write_todos' }),
        end: row('e', 'tool_result', { tool_name: 'write_todos' }),
        plan: {
          kind: 'stream_event',
          id: 'p',
          event: {
            id: 'p',
            eventType: 'todos_update',
            content: '',
            metadata: { todos: [
              { content: 'Read the skill', status: 'completed' },
              { content: 'Write the mapping', status: 'pending' },
            ] },
          },
        },
      },
    }));
    expect(html).not.toContain('Write Todos');
    // Nothing in progress: the next pending item is the current one.
    expect(html).toMatch(/tool-call-entry__title">Write the mapping</);
    expect(html).toContain('1/2');
    expect(html).toContain('todo-panel__item--completed');
  });
});
