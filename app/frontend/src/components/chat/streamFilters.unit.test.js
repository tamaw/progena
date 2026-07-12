import { describe, it, expect } from 'vitest';
import {
  PERSISTED_CATEGORIES,
  categoryForEventType,
  isEventVisible,
  loadStreamFilters,
} from './streamFilters';

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

  it('buckets call_agent under its own "a2a" category, not "tools"', () => {
    expect(categoryForEventType('a2a_call_progress')).toBe('a2a');
    expect(categoryForEventType('tool_start', { tool_name: 'call_agent' })).toBe('a2a');
    expect(categoryForEventType('tool_result', { tool_name: 'call_agent' })).toBe('a2a');
    // Takes priority over the generic tool_error -> 'errors' bucket, the
    // same way subagent_tool_error stays under 'subagents' rather than
    // 'errors' -- a family with its own category owns its own failures too.
    expect(categoryForEventType('tool_error', { tool_name: 'call_agent' })).toBe('a2a');
  });

  it('leaves non-call_agent tool events under "tools"', () => {
    expect(categoryForEventType('tool_start', { tool_name: 'shell' })).toBe('tools');
    expect(categoryForEventType('tool_error', { tool_name: 'shell' })).toBe('errors');
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
