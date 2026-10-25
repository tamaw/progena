import { describe, it, expect } from 'vitest';
import {
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

  it('counts an A2A start/result pair once under delegations, not tools', () => {
    const counts = countCategorizedEvents(
      [
        { eventType: 'tool_start', metadata: { tool_name: 'a2a_send' } },
        { eventType: 'tool_result', metadata: { tool_name: 'a2a_send' } },
      ],
      emptyCounts(),
    );
    expect(counts.delegations).toBe(1);
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
    expect(counts.delegations).toBe(1);
    expect(counts.tools).toBe(0);
  });

  it('does not double-count a failed tool invocation', () => {
    const counts = countCategorizedEvents(
      [
        { eventType: 'tool_start', metadata: { tool_name: 'shell' } },
        { eventType: 'tool_error', metadata: { tool_name: 'shell' } },
      ],
      emptyCounts(),
    );
    expect(counts.tools).toBe(1);
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
    expect(counts.delegations).toBe(1);
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
    expect(counts.delegations).toBe(1);
  });

  it('keeps tool and delegation invocation counts independent of lifecycle updates', () => {
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
    expect(counts.delegations).toBe(6);
    expect(counts.tools + counts.delegations).toBe(10);
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
  it('combines subagents and A2A without hiding ordinary tool activity', () => {
    const filters = { delegations: false };
    expect(isEventVisible({ eventType: 'subagent_start' }, filters)).toBe(false);
    expect(isEventVisible({ eventType: 'tool_start', metadata: { tool_name: 'a2a_send' } }, filters)).toBe(false);
    expect(isEventVisible({ eventType: 'tool_start', metadata: { tool_name: 'read_file' } }, filters)).toBe(true);
  });
  it.each([
    { eventType: 'tool_error', metadata: { tool_name: 'read_file' } },
    { eventType: 'tool_error', metadata: { tool_name: 'a2a_send' } },
    { eventType: 'subagent_result', metadata: { success: false } },
    { eventType: 'subagent_tool_error' },
    { eventType: 'a2a_call_progress', metadata: { state: 'failed' } },
  ])('keeps failures visible when their category is hidden: $eventType', (event) => {
    const filters = { tools: false, delegations: false };
    expect(isEventVisible(event, filters)).toBe(true);
    expect(isEventVisible(event, { ...filters, errors: false })).toBe(false);
  });
  it('lets Errors hide failures even when their activity category is enabled', () => {
    expect(isEventVisible({ eventType: 'tool_error' }, { tools: true, errors: false })).toBe(false);
  });
  it('requires opting in to diagnostic events without hiding failures', () => {
    const event = { eventType: 'node_update' };
    expect(isEventVisible(event, {})).toBe(false);
    expect(isEventVisible(event, { debug: true })).toBe(true);
    expect(isEventVisible({ ...event, metadata: { error: 'Execution failed' } }, {})).toBe(true);
  });
});

describe('loadStreamFilters', () => {
  it('respects stored boolean flags and defaults the rest to true', () => {
    localStorage.setItem('progena:test-filters', JSON.stringify({ tools: false }));
    const filters = loadStreamFilters('progena:test-filters');
    expect(filters.tools).toBe(false);
    expect(filters.reasoning).toBe(true);
  });
  it('falls back to defaults on malformed JSON', () => {
    localStorage.setItem('progena:test-filters', '{bad json');
    const filters = loadStreamFilters('progena:test-filters');
    expect(filters.tools).toBe(true);
  });
  it('drops obsolete conversation flags and merges old delegation preferences', () => {
    const key = 'progena:test-filters';
    localStorage.setItem(key, JSON.stringify({
      user: false, agent: false, images: false, errors: false, debug: true,
      subagents: false, a2a: false, tools: false,
    }));
    expect(loadStreamFilters(key)).toEqual({
      tools: false, plan: true, reasoning: true, delegations: false,
      errors: false, debug: true,
    });
    localStorage.setItem(key, JSON.stringify({ subagents: false, a2a: true }));
    expect(loadStreamFilters(key).delegations).toBe(true);
  });
  it('retains explicit new preferences across reloads', () => {
    const key = 'progena:test-filters';
    const saved = {
      tools: false, plan: true, reasoning: false, delegations: false,
      errors: false, debug: true,
    };
    localStorage.setItem(key, JSON.stringify(saved));
    expect(loadStreamFilters(key)).toEqual(saved);
  });
});
