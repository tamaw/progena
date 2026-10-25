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
  it('shows each call on one line (commentary as its text), opening to the commentary, input box and output box', () => {
    const html = renderToStaticMarkup(React.createElement(ToolCallEntry, {
      item: {
        kind: 'tool_call',
        id: 'call-c',
        start: row('s', 'tool_start', { args_preview: { path: '/config/a.json' }, commentary: 'Reading the config' }),
        end: row('e', 'tool_result', { result_preview: 'line one', duration_ms: 12 }),
      },
    }));

    expect(html.match(/entry__role/g)).toHaveLength(1);
    expect(html).toContain('Read File');
    const summaryPos = html.indexOf('Reading the config');
    const inputPos = html.indexOf('tool-io__label">Input');
    const outputPos = html.indexOf('tool-io__label">Output');
    expect(summaryPos).toBeGreaterThan(-1);
    expect(inputPos).toBeGreaterThan(summaryPos);
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
