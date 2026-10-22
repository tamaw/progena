import { describe, it, expect } from 'vitest';
import {
  PERSISTED_CATEGORIES,
  STREAM_EVENT_CATEGORIES,
  categoryForEventType,
  countCategorizedEvents,
  isEventVisible,
  loadStreamFilters,
} from '@frontend/features/chat/models/streamFilters';
import { A2A_TOOL_NAMES, isA2AToolName } from '@frontend/features/chat/models/a2aTools';

function emptyCounts() {
  return STREAM_EVENT_CATEGORIES.reduce((acc, c) => { acc[c.id] = 0; return acc; }, {});
}

describe('categoryForEventType', () => {
  it('maps event types to categories', () => {
    expect(categoryForEventType('message_delta')).toBeNull();
    expect(categoryForEventType('assistant_metadata')).toBeNull();
    expect(categoryForEventType('node_update')).toBe('debug');
    expect(categoryForEventType('reasoning_delta')).toBe('reasoning');
    expect(categoryForEventType('reasoning_summary')).toBe('reasoning');
    expect(categoryForEventType('tool_error')).toBe('errors');
    expect(categoryForEventType('tool_start')).toBe('tools');
    expect(categoryForEventType('tool_result')).toBe('tools');
    expect(categoryForEventType('todos_update')).toBe('plan');
    expect(categoryForEventType('something_else')).toBe('debug');
    expect(categoryForEventType(123)).toBe('debug');
  });

  it('buckets every A2A tool under its own "a2a" category, not "tools"', () => {
    expect(categoryForEventType('a2a_call_progress')).toBe('a2a');
    for (const toolName of A2A_TOOL_NAMES) {
      expect(isA2AToolName(toolName)).toBe(true);
      expect(categoryForEventType('tool_start', { tool_name: toolName })).toBe('a2a');
      expect(categoryForEventType('tool_result', { tool_name: toolName })).toBe('a2a');
      expect(categoryForEventType('tool_error', { tool_name: toolName })).toBe('a2a');
    }
  });

  it('leaves non-A2A tool events under "tools"', () => {
    expect(categoryForEventType('tool_start', { tool_name: 'shell' })).toBe('tools');
    expect(categoryForEventType('tool_error', { tool_name: 'shell' })).toBe('errors');
    expect(isA2AToolName('shell')).toBe(false);
  });
});

describe('countCategorizedEvents', () => {
  it('counts a tool_start/tool_result pair once, not twice', () => {
    const counts = countCategorizedEvents(
      [
        { eventType: 'tool_start', metadata: { tool_name: 'shell' } },
        { eventType: 'tool_result', metadata: { tool_name: 'shell' } },
      ],
      emptyCounts(),
    );
    expect(counts.tools).toBe(1);
  });

  it('counts an A2A start/result pair once under "a2a", not "tools"', () => {
    const counts = countCategorizedEvents(
      [
        { eventType: 'tool_start', metadata: { tool_name: 'a2a_send' } },
        { eventType: 'tool_result', metadata: { tool_name: 'a2a_send' } },
      ],
      emptyCounts(),
    );
    expect(counts.a2a).toBe(1);
    expect(counts.tools).toBe(0);
  });

  it('does not double-count an A2A invocation that also errors', () => {
    const counts = countCategorizedEvents(
      [
        { eventType: 'tool_start', metadata: { tool_name: 'a2a_send' } },
        { eventType: 'tool_error', metadata: { tool_name: 'a2a_send' } },
      ],
      emptyCounts(),
    );
    expect(counts.a2a).toBe(1);
    expect(counts.errors).toBe(0);
  });

  it('still counts a plain tool error under "errors"', () => {
    const counts = countCategorizedEvents(
      [
        { eventType: 'tool_start', metadata: { tool_name: 'shell' } },
        { eventType: 'tool_error', metadata: { tool_name: 'shell' } },
      ],
      emptyCounts(),
    );
    expect(counts.tools).toBe(1);
    expect(counts.errors).toBe(1);
  });

  it('counts intermediate a2a_call_progress updates as part of the same invocation', () => {
    const counts = countCategorizedEvents(
      [
        { eventType: 'tool_start', metadata: { tool_name: 'a2a_send' } },
        { eventType: 'a2a_call_progress', metadata: { tool_call_id: 'x' } },
        { eventType: 'a2a_call_progress', metadata: { tool_call_id: 'x' } },
        { eventType: 'tool_result', metadata: { tool_name: 'a2a_send' } },
      ],
      emptyCounts(),
    );
    expect(counts.a2a).toBe(1);
  });

  it('counts a subagent dispatch once, ignoring its nested tool calls and message deltas', () => {
    const counts = countCategorizedEvents(
      [
        { eventType: 'subagent_start', metadata: {} },
        { eventType: 'subagent_tool_start', metadata: {} },
        { eventType: 'subagent_tool_result', metadata: {} },
        { eventType: 'subagent_message_delta', metadata: {} },
        { eventType: 'subagent_message_delta', metadata: {} },
        { eventType: 'subagent_result', metadata: {} },
      ],
      emptyCounts(),
    );
    expect(counts.subagents).toBe(1);
  });

  it('reproduces the reported header/filter mismatch: 4 tools + 6 a2a sums to the header\'s 10, not the raw event total', () => {
    const events = [
      { eventType: 'tool_start', metadata: { tool_name: 'list_agents' } },
      { eventType: 'tool_result', metadata: { tool_name: 'list_agents' } },
      { eventType: 'tool_start', metadata: { tool_name: 'write_todos' } },
      { eventType: 'tool_result', metadata: { tool_name: 'write_todos' } },
      { eventType: 'tool_start', metadata: { tool_name: 'write_todos' } },
      { eventType: 'tool_result', metadata: { tool_name: 'write_todos' } },
      { eventType: 'tool_start', metadata: { tool_name: 'write_todos' } },
      { eventType: 'tool_result', metadata: { tool_name: 'write_todos' } },
    ];
    for (let i = 0; i < 6; i += 1) {
      events.push({ eventType: 'tool_start', metadata: { tool_name: 'a2a_send' } });
      events.push({ eventType: 'tool_result', metadata: { tool_name: 'a2a_send' } });
    }
    const counts = countCategorizedEvents(events, emptyCounts());
    expect(counts.tools).toBe(4);
    expect(counts.a2a).toBe(6);
    expect(counts.tools + counts.a2a).toBe(10);
  });

  it('ignores reasoning and null-category events', () => {
    const counts = countCategorizedEvents(
      [
        { eventType: 'reasoning_delta', metadata: {} },
        { eventType: 'message_delta', metadata: {} },
      ],
      emptyCounts(),
    );
    expect(Object.values(counts).every((v) => v === 0)).toBe(true);
  });

  it('tolerates a non-array input', () => {
    const counts = countCategorizedEvents(undefined, emptyCounts());
    expect(Object.values(counts).every((v) => v === 0)).toBe(true);
  });
});

describe('isEventVisible', () => {
  it('hides events whose category is filtered off', () => {
    expect(isEventVisible({ eventType: 'tool_start' }, { tools: false })).toBe(false);
    expect(isEventVisible({ eventType: 'tool_start' }, { tools: true })).toBe(true);
  });
  it('treats message_delta (null category) as not visible', () => {
    expect(isEventVisible({ eventType: 'message_delta' }, {})).toBe(false);
  });
  it('defaults to visible when the category flag is absent', () => {
    expect(isEventVisible({ eventType: 'reasoning_delta' }, {})).toBe(true);
  });
});

describe('loadStreamFilters', () => {
  it('defaults every category to true when nothing stored', () => {
    const filters = loadStreamFilters('progenitor:test-filters');
    expect(Object.values(filters).every((v) => v === true)).toBe(true);
  });
  it('respects stored boolean flags and defaults the rest to true', () => {
    localStorage.setItem('progenitor:test-filters', JSON.stringify({ tools: false }));
    const filters = loadStreamFilters('progenitor:test-filters');
    expect(filters.tools).toBe(false);
    expect(filters.reasoning).toBe(true);
  });
  it('falls back to defaults on malformed JSON', () => {
    localStorage.setItem('progenitor:test-filters', '{bad json');
    const filters = loadStreamFilters('progenitor:test-filters');
    expect(filters.tools).toBe(true);
  });
});

describe('PERSISTED_CATEGORIES', () => {
  it('persists tools, plan, reasoning, subagents and a2a', () => {
    expect(PERSISTED_CATEGORIES.has('tools')).toBe(true);
    expect(PERSISTED_CATEGORIES.has('reasoning')).toBe(true);
    expect(PERSISTED_CATEGORIES.has('a2a')).toBe(true);
    expect(PERSISTED_CATEGORIES.has('debug')).toBe(false);
  });
});
