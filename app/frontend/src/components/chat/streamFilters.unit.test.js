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
    expect(categoryForEventType('node_update')).toBe('debug');
    expect(categoryForEventType('reasoning_delta')).toBe('reasoning');
    expect(categoryForEventType('reasoning_summary')).toBe('reasoning');
    expect(categoryForEventType('tool_error')).toBe('errors');
    expect(categoryForEventType('tool_start')).toBe('tools');
    expect(categoryForEventType('tool_result')).toBe('tools');
    expect(categoryForEventType('something_else')).toBe('debug');
    expect(categoryForEventType(123)).toBe('debug');
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
  it('persists only tools and reasoning', () => {
    expect(PERSISTED_CATEGORIES.has('tools')).toBe(true);
    expect(PERSISTED_CATEGORIES.has('reasoning')).toBe(true);
    expect(PERSISTED_CATEGORIES.has('debug')).toBe(false);
  });
});
