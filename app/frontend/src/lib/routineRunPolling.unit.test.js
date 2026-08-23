import { describe, expect, it } from 'vitest';

import {
  findRunBySessionId,
  latestCompletionMs,
  parseRunThreadId,
  randomSessionId,
  totalRunMetaEntries,
} from './routineRunPolling';

describe('parseRunThreadId', () => {
  it('splits a routine thread_id into routineId and revisionId', () => {
    expect(parseRunThreadId('routine:tmp-retry-loop:abc123:session-1')).toEqual({
      routineId: 'tmp-retry-loop',
      revisionId: 'abc123',
    });
  });

  it('maps the "unpinned" sentinel to a null revisionId', () => {
    expect(parseRunThreadId('routine:tmp-retry-loop:unpinned:session-1')).toEqual({
      routineId: 'tmp-retry-loop',
      revisionId: null,
    });
  });

  it('returns null for anything that does not parse, rather than throwing', () => {
    expect(parseRunThreadId(null)).toBeNull();
    expect(parseRunThreadId(undefined)).toBeNull();
    expect(parseRunThreadId('not-a-thread-id')).toBeNull();
    expect(parseRunThreadId('routine:only-two-parts')).toBeNull();
  });
});

describe('randomSessionId', () => {
  it('produces distinct ids across calls', () => {
    const ids = new Set(Array.from({ length: 20 }, () => randomSessionId()));
    expect(ids.size).toBe(20);
  });

  it('never contains a literal hyphen (used as the thread_id delimiter)', () => {
    for (let i = 0; i < 20; i += 1) {
      expect(randomSessionId()).not.toContain('-');
    }
  });
});

describe('findRunBySessionId', () => {
  const runs = [
    { thread_id: 'routine:r:rev-a:session-1', run_id: 'session-1' },
    { thread_id: 'routine:r:rev-b:session-2', run_id: 'session-2' },
  ];

  it('matches the run whose run_id (thread_id trailing segment) equals sessionId', () => {
    expect(findRunBySessionId(runs, 'session-2')).toEqual(runs[1]);
  });

  it('returns null when no run matches yet', () => {
    expect(findRunBySessionId(runs, 'not-there')).toBeNull();
  });

  it('returns null for a falsy sessionId or runs list, never throws', () => {
    expect(findRunBySessionId(runs, null)).toBeNull();
    expect(findRunBySessionId(null, 'session-1')).toBeNull();
  });
});

describe('totalRunMetaEntries', () => {
  it('sums entries across every node, including loop nodes with multiple entries', () => {
    const runMeta = {
      a: [{ duration_ms: 1 }],
      b: [{ duration_ms: 1 }, { duration_ms: 1 }, { duration_ms: 1 }],
    };
    expect(totalRunMetaEntries(runMeta)).toBe(4);
  });

  it('is 0 for empty/missing run_meta', () => {
    expect(totalRunMetaEntries({})).toBe(0);
    expect(totalRunMetaEntries(undefined)).toBe(0);
  });
});

describe('latestCompletionMs', () => {
  it('is null when nothing has completed yet', () => {
    expect(latestCompletionMs({})).toBeNull();
    expect(latestCompletionMs(undefined)).toBeNull();
  });

  it('picks the latest started_at + duration_ms across every node/entry', () => {
    const runMeta = {
      a: [{ started_at: '2026-01-01T00:00:00.000Z', duration_ms: 1000 }],
      b: [{ started_at: '2026-01-01T00:00:02.000Z', duration_ms: 500 }],
    };
    // a finishes at 00:00:01.000, b finishes at 00:00:02.500 -- b wins.
    expect(latestCompletionMs(runMeta)).toBe(Date.parse('2026-01-01T00:00:02.500Z'));
  });

  it('ignores an entry with an unparseable started_at rather than throwing', () => {
    const runMeta = { a: [{ started_at: 'not-a-date', duration_ms: 1000 }] };
    expect(latestCompletionMs(runMeta)).toBeNull();
  });
});
