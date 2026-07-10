import { describe, it, expect } from 'vitest';
import {
  MAX_STREAM_EVENTS_PER_MESSAGE,
  CANCELLED_SUFFIX,
  isReasoningEvent,
  hasReasoningContent,
  countReasoningTimelineEntries,
  normalizeStreamEvent,
  extractReasoningTokenCount,
  appendReasoningText,
  createStreamEvent,
  appendStreamEvent,
} from './streamEventsModel';

const evt = (overrides = {}) => ({
  id: 'e',
  eventType: 'tool_start',
  content: '',
  metadata: {},
  ...overrides,
});

describe('isReasoningEvent / hasReasoningContent', () => {
  it('detects reasoning event types', () => {
    expect(isReasoningEvent({ eventType: 'reasoning_delta' })).toBe(true);
    expect(isReasoningEvent({ eventType: 'reasoning_summary' })).toBe(true);
    expect(isReasoningEvent({ eventType: 'tool_start' })).toBe(false);
    expect(isReasoningEvent(null)).toBe(false);
  });

  it('requires non-empty trimmed content', () => {
    expect(hasReasoningContent({ content: 'hi' })).toBe(true);
    expect(hasReasoningContent({ content: '   ' })).toBe(false);
    expect(hasReasoningContent({ content: 123 })).toBe(false);
  });
});

describe('countReasoningTimelineEntries', () => {
  it('counts rendered reasoning events from stream events', () => {
    const events = [
      { eventType: 'reasoning_delta', content: 'abc' },
      { eventType: 'tool_start', content: 'search' },
    ];
    expect(countReasoningTimelineEntries(events, 'fallback')).toBe(1);
  });

  it('falls back to reasoningText when no stream reasoning event exists', () => {
    expect(countReasoningTimelineEntries([], 'saved reasoning')).toBe(1);
    expect(countReasoningTimelineEntries([], '   ')).toBe(0);
  });
});

describe('normalizeStreamEvent', () => {
  it('returns null for non-objects', () => {
    expect(normalizeStreamEvent(null, 0)).toBeNull();
    expect(normalizeStreamEvent('x', 0)).toBeNull();
  });

  it('maps event_type and coerces metadata/content', () => {
    const n = normalizeStreamEvent({ id: 'k', event_type: 'tool_result', content: 7, metadata: [1] }, 0);
    expect(n.id).toBe('k');
    expect(n.eventType).toBe('tool_result');
    expect(n.content).toBe(''); // non-string content coerced to ''
    expect(n.metadata).toEqual({}); // array metadata coerced to {}
  });

  it('defaults eventType and generates an id when absent', () => {
    const n = normalizeStreamEvent({ metadata: { a: 1 } }, 3);
    expect(n.eventType).toBe('stream_event');
    expect(typeof n.id).toBe('string');
    expect(n.metadata).toEqual({ a: 1 });
  });
});

describe('extractReasoningTokenCount', () => {
  it('returns a positive finite count or null', () => {
    expect(extractReasoningTokenCount({ metadata: { reasoning_tokens: 12 } })).toBe(12);
    expect(extractReasoningTokenCount({ metadata: { reasoning_tokens: 0 } })).toBeNull();
    expect(extractReasoningTokenCount({ metadata: {} })).toBeNull();
  });
});

describe('appendReasoningText', () => {
  it('concatenates deltas', () => {
    let text = '';
    text = appendReasoningText(text, { eventType: 'reasoning_delta', content: 'ab' });
    text = appendReasoningText(text, { eventType: 'reasoning_delta', content: 'cd' });
    expect(text).toBe('abcd');
  });

  it('ignores non-reasoning or empty events', () => {
    expect(appendReasoningText('x', { eventType: 'tool_start', content: 'y' })).toBe('x');
    expect(appendReasoningText('x', { eventType: 'reasoning_delta', content: '  ' })).toBe('x');
  });

  it('summary replaces only when longer than the accumulated text', () => {
    expect(appendReasoningText('short', { eventType: 'reasoning_summary', content: 'a much longer summary' }))
      .toBe('a much longer summary');
    expect(appendReasoningText('a much longer existing text', { eventType: 'reasoning_summary', content: 'tiny' }))
      .toBe('a much longer existing text');
    expect(appendReasoningText('', { eventType: 'reasoning_summary', content: 'first' })).toBe('first');
  });
});

describe('createStreamEvent', () => {
  it('returns null for non-objects', () => {
    expect(createStreamEvent(null)).toBeNull();
  });

  it('builds an event and attaches images when present', () => {
    const e = createStreamEvent({ event_type: 'tool_result', content: 'ok', metadata: { a: 1 }, images: [{ base64: 'x' }] });
    expect(e.eventType).toBe('tool_result');
    expect(e.content).toBe('ok');
    expect(e.metadata).toEqual({ a: 1 });
    expect(e.images).toEqual([{ base64: 'x' }]);
  });

  it('omits images when none', () => {
    const e = createStreamEvent({ event_type: 'tool_start' });
    expect(e).not.toHaveProperty('images');
  });
});

describe('appendStreamEvent', () => {
  it('drops message_delta events', () => {
    const events = [evt({ id: 'a' })];
    expect(appendStreamEvent(events, evt({ eventType: 'message_delta' }))).toBe(events);
  });

  it('appends a distinct event', () => {
    const out = appendStreamEvent([evt({ id: 'a', content: 'x' })], evt({ id: 'b', content: 'y' }));
    expect(out).toHaveLength(2);
  });

  it('dedupes a consecutive identical event', () => {
    const a = evt({ id: 'a', eventType: 'tool_start', content: 'same', metadata: { t: 1 } });
    const dup = evt({ id: 'b', eventType: 'tool_start', content: 'same', metadata: { t: 1 } });
    const out = appendStreamEvent([a], dup);
    expect(out).toHaveLength(1);
  });

  it('merges consecutive reasoning deltas into one entry', () => {
    let events = [];
    events = appendStreamEvent(events, evt({ id: 'r1', eventType: 'reasoning_delta', content: 'foo' }));
    events = appendStreamEvent(events, evt({ id: 'r2', eventType: 'reasoning_delta', content: 'bar' }));
    expect(events).toHaveLength(1);
    expect(events[0].content).toBe('foobar');
  });

  it('skips an empty reasoning event with no prior reasoning', () => {
    const out = appendStreamEvent([], evt({ eventType: 'reasoning_delta', content: '' }));
    expect(out).toHaveLength(0);
  });

  it('caps the timeline at MAX_STREAM_EVENTS_PER_MESSAGE keeping the newest', () => {
    let events = [];
    for (let i = 0; i < MAX_STREAM_EVENTS_PER_MESSAGE + 5; i += 1) {
      events = appendStreamEvent(events, evt({ id: `e${i}`, content: `c${i}` }));
    }
    expect(events).toHaveLength(MAX_STREAM_EVENTS_PER_MESSAGE);
    expect(events[events.length - 1].content).toBe(`c${MAX_STREAM_EVENTS_PER_MESSAGE + 4}`);
    expect(events[0].content).toBe('c5'); // oldest 5 dropped
  });
});

