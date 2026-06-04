import { describe, it, expect } from 'vitest';
import {
  CHAT_AGENT_NAME,
  toValidTimestamp,
  deriveChatTitle,
  countToolInvocations,
  getTimeBucket,
  groupSessionItems,
} from './conversationModel';

describe('toValidTimestamp', () => {
  it('passes finite numbers through', () => {
    expect(toValidTimestamp(1700000000000)).toBe(1700000000000);
  });
  it('parses date strings', () => {
    expect(toValidTimestamp('2026-06-04T00:00:00Z')).toBe(Date.parse('2026-06-04T00:00:00Z'));
  });
  it('returns null for invalid input', () => {
    expect(toValidTimestamp('not-a-date')).toBeNull();
    expect(toValidTimestamp({})).toBeNull();
    expect(toValidTimestamp(undefined)).toBeNull();
  });
});

describe('deriveChatTitle', () => {
  it('defaults when there is no user message', () => {
    expect(deriveChatTitle([])).toBe('New Progenitor conversation');
    expect(deriveChatTitle([{ role: 'assistant', content: 'hi' }])).toBe('New Progenitor conversation');
  });
  it('uses the first user message, whitespace-collapsed', () => {
    expect(deriveChatTitle([{ role: 'user', content: '  hello   world ' }])).toBe('hello world');
  });
  it('truncates long titles to 69 chars + ellipsis', () => {
    const long = 'x'.repeat(100);
    const title = deriveChatTitle([{ role: 'user', content: long }]);
    expect(title).toHaveLength(72);
    expect(title.endsWith('...')).toBe(true);
  });
});

describe('countToolInvocations', () => {
  it('counts tool_start events across messages', () => {
    const messages = [
      { streamEvents: [{ eventType: 'tool_start' }, { eventType: 'tool_result' }] },
      { streamEvents: [{ eventType: 'tool_start' }, { eventType: 'reasoning_delta' }] },
      { streamEvents: [] },
      {},
    ];
    expect(countToolInvocations(messages)).toBe(2);
  });
});

describe('getTimeBucket', () => {
  const now = new Date(2026, 5, 4, 12, 0, 0).getTime(); // local 2026-06-04 12:00
  const startOfToday = new Date(2026, 5, 4, 0, 0, 0).getTime();
  const day = 24 * 60 * 60 * 1000;

  it('classifies relative to the start of today', () => {
    expect(getTimeBucket(now, now)).toBe('today');
    expect(getTimeBucket(startOfToday - 1, now)).toBe('yesterday');
    expect(getTimeBucket(startOfToday - 3 * day, now)).toBe('last-week');
    expect(getTimeBucket(startOfToday - 10 * day, now)).toBe('earlier');
  });

  it('treats missing timestamps as today', () => {
    expect(getTimeBucket(null, now)).toBe('today');
  });
});

describe('groupSessionItems', () => {
  it('buckets items and keeps SIDEBAR order, omitting empty groups', () => {
    const groups = groupSessionItems([
      { id: 'p', pinned: true, bucket: 'earlier' },
      { id: 't', pinned: false, bucket: 'today' },
      { id: 'e', pinned: false, bucket: 'earlier' },
    ]);
    expect(groups.map((g) => g.key)).toEqual(['pinned', 'today', 'earlier']);
    expect(groups[0].items.map((i) => i.id)).toEqual(['p']);
  });
});

describe('CHAT_AGENT_NAME', () => {
  it('is Progenitor', () => {
    expect(CHAT_AGENT_NAME).toBe('Progenitor');
  });
});
