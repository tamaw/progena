import { describe, expect, it } from 'vitest';
import {
  filterInboxMessages,
  getInboxMessageStatus,
  inboxMessageNeedsDecision,
  matchesInboxFilter,
  matchesInboxQuery,
  sortInboxMessages,
} from '@frontend/features/inbox/inboxPageModel';

describe('getInboxMessageStatus', () => {
  it('is unread for an unread message', () => {
    expect(getInboxMessageStatus({ read: false })).toBe('unread');
  });

  it('is read for a read message', () => {
    expect(getInboxMessageStatus({ read: true })).toBe('read');
  });

  it('is unread regardless of approval/decision fields', () => {
    expect(getInboxMessageStatus({ read: false, requires_approval: true, decision: null })).toBe('unread');
    expect(getInboxMessageStatus({ read: true, requires_approval: true, decision: 'approved' })).toBe('read');
  });

  it('defaults to unread for a missing message', () => {
    expect(getInboxMessageStatus(null)).toBe('unread');
    expect(getInboxMessageStatus(undefined)).toBe('unread');
  });
});

describe('inboxMessageNeedsDecision', () => {
  it('is true for an approval-required message with no decision yet', () => {
    expect(inboxMessageNeedsDecision({ requires_approval: true, decision: null })).toBe(true);
  });

  it('is false once a decision has been made', () => {
    expect(inboxMessageNeedsDecision({ requires_approval: true, decision: 'approved' })).toBe(false);
    expect(inboxMessageNeedsDecision({ requires_approval: true, decision: 'rejected' })).toBe(false);
  });

  it('is false for a message that never required approval', () => {
    expect(inboxMessageNeedsDecision({ requires_approval: false })).toBe(false);
  });

  it('is independent of read/unread', () => {
    expect(inboxMessageNeedsDecision({ read: true, requires_approval: true, decision: null })).toBe(true);
    expect(inboxMessageNeedsDecision({ read: false, requires_approval: true, decision: null })).toBe(true);
  });

  it('defaults to false for a missing message', () => {
    expect(inboxMessageNeedsDecision(null)).toBe(false);
  });
});

describe('matchesInboxFilter', () => {
  const unread = { id: 'a', read: false };
  const read = { id: 'b', read: true };
  const undecided = { id: 'c', read: false, requires_approval: true, decision: null };
  const readUndecided = { id: 'f', read: true, requires_approval: true, decision: null };
  const decided = { id: 'g', read: true, requires_approval: true, decision: 'approved' };
  const archived = { id: 'e', read: true, archived: true };

  it('"all" includes everything not archived', () => {
    expect(matchesInboxFilter(unread, 'all')).toBe(true);
    expect(matchesInboxFilter(read, 'all')).toBe(true);
    expect(matchesInboxFilter(undecided, 'all')).toBe(true);
    expect(matchesInboxFilter(archived, 'all')).toBe(false);
  });

  it('"unread" matches unread messages, approval-required or not', () => {
    expect(matchesInboxFilter(unread, 'unread')).toBe(true);
    expect(matchesInboxFilter(undecided, 'unread')).toBe(true);
    expect(matchesInboxFilter(read, 'unread')).toBe(false);
  });

  it('"pending" matches any undecided approval message, read or unread', () => {
    expect(matchesInboxFilter(undecided, 'pending')).toBe(true);
    expect(matchesInboxFilter(readUndecided, 'pending')).toBe(true);
    expect(matchesInboxFilter(decided, 'pending')).toBe(false);
    expect(matchesInboxFilter(unread, 'pending')).toBe(false);
  });

  it('"archived" only matches archived messages, overriding other statuses', () => {
    expect(matchesInboxFilter(archived, 'archived')).toBe(true);
    expect(matchesInboxFilter(unread, 'archived')).toBe(false);
  });

  it('excludes archived messages from the unread filter', () => {
    const archivedUnread = { ...unread, archived: true };
    expect(matchesInboxFilter(archivedUnread, 'unread')).toBe(false);
  });
});

describe('matchesInboxQuery', () => {
  const message = { title: 'Weekly digest', body: 'Deploy the release candidate' };

  it('matches on title', () => {
    expect(matchesInboxQuery(message, 'weekly')).toBe(true);
  });

  it('matches on body', () => {
    expect(matchesInboxQuery(message, 'release candidate')).toBe(true);
  });

  it('is case-insensitive', () => {
    expect(matchesInboxQuery(message, 'DIGEST')).toBe(true);
  });

  it('returns true for an empty query', () => {
    expect(matchesInboxQuery(message, '')).toBe(true);
    expect(matchesInboxQuery(message, '   ')).toBe(true);
  });

  it('returns false when nothing matches', () => {
    expect(matchesInboxQuery(message, 'nonexistent')).toBe(false);
  });
});

describe('filterInboxMessages', () => {
  const messages = [
    { id: 'a', title: 'Alpha', read: false },
    { id: 'b', title: 'Beta', read: true },
    { id: 'c', title: 'Gamma', read: false, requires_approval: true, decision: null },
    { id: 'd', title: 'Delta', read: true, archived: true },
  ];

  it('combines filter and query', () => {
    expect(filterInboxMessages(messages, 'all', '').map((m) => m.id)).toEqual(['a', 'b', 'c']);
    expect(filterInboxMessages(messages, 'unread', '').map((m) => m.id)).toEqual(['a', 'c']);
    expect(filterInboxMessages(messages, 'archived', '').map((m) => m.id)).toEqual(['d']);
    expect(filterInboxMessages(messages, 'all', 'gamma').map((m) => m.id)).toEqual(['c']);
  });

  it('returns an empty array for non-array input', () => {
    expect(filterInboxMessages(null, 'all', '')).toEqual([]);
  });
});

describe('sortInboxMessages', () => {
  it('sorts alphabetically by title in "Name" mode', () => {
    const messages = [{ title: 'Zeta' }, { title: 'Alpha' }, { title: 'Mu' }];
    expect(sortInboxMessages(messages, 'Name').map((m) => m.title)).toEqual(['Alpha', 'Mu', 'Zeta']);
  });

  it('leaves server order (created_at desc) unchanged in "Recent" mode', () => {
    const messages = [{ title: 'Zeta' }, { title: 'Alpha' }];
    expect(sortInboxMessages(messages, 'Recent').map((m) => m.title)).toEqual(['Zeta', 'Alpha']);
  });
});
