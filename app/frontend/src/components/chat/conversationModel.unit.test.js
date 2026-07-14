import { describe, it, expect } from 'vitest';
import {
  CHAT_AGENT_NAME,
  DEFAULT_CHAT_TITLE,
  toValidTimestamp,
  deriveChatTitle,
  collectMessageImages,
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
    expect(deriveChatTitle([])).toBe(DEFAULT_CHAT_TITLE);
    expect(deriveChatTitle([{ role: 'assistant', content: 'hi' }])).toBe(DEFAULT_CHAT_TITLE);
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

describe('collectMessageImages', () => {
  it('collects tool-result images only from assistant messages', () => {
    const userMessage = {
      role: 'user',
      content: 'hi',
      streamEvents: [{ eventType: 'tool_result', images: [{ base64: 'x', media_type: 'image/png' }] }],
    };
    expect(collectMessageImages(userMessage)).toEqual([]);
  });

  it('builds a data: URI per tool-result image, in event order', () => {
    const message = {
      role: 'assistant',
      content: 'here you go',
      streamEvents: [
        { eventType: 'tool_result', images: [{ id: 'a', base64: 'AAA', media_type: 'image/png' }] },
        { eventType: 'tool_result', images: [] },
        { eventType: 'tool_result', images: [{ id: 'b', base64: 'BBB' }] },
      ],
    };
    expect(collectMessageImages(message)).toEqual([
      { id: 'a', src: 'data:image/png;base64,AAA', alt: 'Image 1' },
      { id: 'b', src: 'data:image/jpeg;base64,BBB', alt: 'Image 2' },
    ]);
  });

  it('returns an empty list for a message with no images anywhere', () => {
    const message = { role: 'assistant', content: 'just text, no images', streamEvents: [] };
    expect(collectMessageImages(message)).toEqual([]);
  });

  it('tolerates a missing streamEvents array', () => {
    expect(collectMessageImages({ role: 'assistant', content: 'hi' })).toEqual([]);
  });

  // Regression: a `task` subagent delegation's own images ride on
  // subagent_result (see _expand_task_subagent_events in
  // deepagent_stream.py, which fully replaces the raw tool_result event
  // with this one), not tool_result -- a filter that only recognized
  // tool_result silently dropped them from both the image strip and the
  // Images filter count.
  it('also collects images from subagent_result events', () => {
    const message = {
      role: 'assistant',
      content: 'delegated',
      streamEvents: [
        { eventType: 'subagent_result', images: [{ id: 'a', base64: 'AAA', media_type: 'image/png' }] },
      ],
    };
    expect(collectMessageImages(message)).toEqual([
      { id: 'a', src: 'data:image/png;base64,AAA', alt: 'Image 1' },
    ]);
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

describe('DEFAULT_CHAT_TITLE', () => {
  it('keeps empty draft conversations consistent across the page', () => {
    expect(DEFAULT_CHAT_TITLE).toBe('New chat');
  });
});
