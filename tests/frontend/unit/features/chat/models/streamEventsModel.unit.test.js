import { describe, it, expect } from 'vitest';
import {
  MAX_STREAM_EVENTS_PER_MESSAGE,
  CANCELLED_SUFFIX,
  isReasoningEvent,
  hasReasoningContent,
  countReasoningTimelineEntries,
  isRenderableStreamEvent,
  normalizeStreamEvent,
  extractReasoningTokenCount,
  appendReasoningText,
  createStreamEvent,
  appendStreamEvent,
} from '@frontend/features/chat/models/streamEventsModel';

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
  it('does not count delta-only reasoning as a rendered timeline entry', () => {
    const events = [
      { eventType: 'reasoning_delta', content: 'abc' },
      { eventType: 'tool_start', content: 'search' },
    ];
    expect(countReasoningTimelineEntries(events, 'fallback')).toBe(0);
  });

  it('counts one terminal reasoning summary after deltas', () => {
    const events = [
      { eventType: 'reasoning_delta', content: 'abc' },
      { eventType: 'reasoning_summary', content: 'complete reasoning' },
    ];
    expect(countReasoningTimelineEntries(events, 'fallback')).toBe(1);
  });

  it('falls back to reasoningText when no stream reasoning event exists', () => {
    expect(countReasoningTimelineEntries([], 'saved reasoning')).toBe(1);
    expect(countReasoningTimelineEntries([], '   ')).toBe(0);
  });
});

describe('isRenderableStreamEvent', () => {
  it('keeps hidden control events out of the visible timeline', () => {
    expect(isRenderableStreamEvent({ eventType: 'message_delta' })).toBe(false);
    expect(isRenderableStreamEvent({ eventType: 'assistant_metadata' })).toBe(false);
    expect(isRenderableStreamEvent({ eventType: 'node_update', metadata: { node_name: 'pending_response' } })).toBe(false);
    expect(isRenderableStreamEvent({ eventType: 'node_update', metadata: {} })).toBe(false);
  });

  it('renders named node updates and ordinary stream rows', () => {
    expect(isRenderableStreamEvent({ eventType: 'node_update', metadata: { node_name: 'planner' } })).toBe(true);
    expect(isRenderableStreamEvent({ eventType: 'tool_result', metadata: { tool_name: 'search_web' } })).toBe(true);
  });
});

describe('normalizeStreamEvent', () => {
  it('returns null for non-objects', () => {
    expect(normalizeStreamEvent(null, 0)).toBeNull();
    expect(normalizeStreamEvent('x', 0)).toBeNull();
  });

  it('maps event_type, coerces metadata/content, and preserves images plus parseable timestamps', () => {
    const isoTimestamp = '2026-07-11T02:31:08.000Z';
    const images = [{ base64: 'abc', media_type: 'image/png' }];
    const n = normalizeStreamEvent({
      id: 'k',
      event_type: 'tool_result',
      content: 7,
      metadata: [1],
      images,
      timestamp: isoTimestamp,
    }, 0);
    expect(n.id).toBe('k');
    expect(n.eventType).toBe('tool_result');
    expect(n.content).toBe('');
    expect(n.metadata).toEqual({});
    expect(n.images).toEqual(images);
    expect(n.timestamp).toBe(Date.parse(isoTimestamp));
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

  it('merges a delta then terminal summary with complete content and summary metadata', () => {
    let events = appendStreamEvent([], evt({
      id: 'r1',
      eventType: 'reasoning_delta',
      content: 'partial thought',
      metadata: { reasoning_tokens: 5, summary: 'delta fragment' },
    }));
    events = appendStreamEvent(events, evt({
      id: 'r2',
      eventType: 'reasoning_summary',
      content: 'complete reasoning trace',
      metadata: {
        reasoning_tokens: 42,
        summary: 'Final reasoning summary',
        detail: 'Provider detail',
      },
    }));

    expect(events[0].id).toBe('r1');
    expect(events).toHaveLength(1);
    expect(events[0].eventType).toBe('reasoning_summary');
    expect(events[0].content).toBe('complete reasoning trace');
    expect(events[0].metadata).toEqual(expect.objectContaining({
      reasoning_tokens: 42,
      summary: 'Final reasoning summary',
      detail: 'Provider detail',
    }));
  });

  it('ignores a reasoning delta that arrives after the terminal summary', () => {
    const summary = evt({
      id: 'summary',
      eventType: 'reasoning_summary',
      content: 'final reasoning',
      metadata: { reasoning_tokens: 10 },
    });
    const lateDelta = evt({
      id: 'late-delta',
      eventType: 'reasoning_delta',
      content: 'late fragment that must be ignored',
      metadata: { reasoning_tokens: 11 },
    });

    const withSummary = appendStreamEvent([], summary);
    const afterDelta = appendStreamEvent(withSummary, lateDelta);

    expect(afterDelta).toHaveLength(1);
    expect(afterDelta[0].id).toBe('summary');
    expect(afterDelta[0].eventType).toBe('reasoning_summary');
    expect(afterDelta[0].content).toBe('final reasoning');
  });

  it('skips an empty reasoning event with no prior reasoning', () => {
    const out = appendStreamEvent([], evt({ eventType: 'reasoning_delta', content: '' }));
    expect(out).toHaveLength(0);
  });

  it('appends a new entry for each todos_update instead of replacing in place', () => {
    const first = evt({ id: 't1', eventType: 'todos_update', metadata: { todos: [{ content: 'a', status: 'pending' }] } });
    let events = appendStreamEvent([], first);
    const second = evt({ id: 't2', eventType: 'todos_update', metadata: { todos: [{ content: 'a', status: 'completed' }] } });
    events = appendStreamEvent(events, second);

    expect(events).toHaveLength(2);
    expect(events[0].id).toBe('t1');
    expect(events[0].metadata.todos).toEqual([{ content: 'a', status: 'pending' }]);
    expect(events[1].id).toBe('t2');
    expect(events[1].metadata.todos).toEqual([{ content: 'a', status: 'completed' }]);
  });

  it('dedupes a consecutive todos_update with identical content', () => {
    const first = evt({ id: 't1', eventType: 'todos_update', metadata: { todos: [{ content: 'a', status: 'pending' }] } });
    let events = appendStreamEvent([], first);
    const dup = evt({ id: 't2', eventType: 'todos_update', metadata: { todos: [{ content: 'a', status: 'pending' }] } });
    events = appendStreamEvent(events, dup);

    expect(events).toHaveLength(1);
  });

  it('appends a todos_update entry alongside other events', () => {
    const events = appendStreamEvent(
      [evt({ id: 'tool1', eventType: 'tool_start' })],
      evt({ id: 't1', eventType: 'todos_update', metadata: { todos: [{ content: 'a', status: 'pending' }] } }),
    );
    expect(events).toHaveLength(2);
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
