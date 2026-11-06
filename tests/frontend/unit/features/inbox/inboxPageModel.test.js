import { describe, expect, it } from 'vitest';
import {
  getInboxMessageStatus,
  inboxMessageNeedsDecision,
} from '@frontend/features/inbox/inboxPageModel';

describe('getInboxMessageStatus', () => {
  it('is unread for an unread message', () => {
    expect(getInboxMessageStatus({ read: false })).toBe('unread');
  });

  it('is read for a read message', () => {
    expect(getInboxMessageStatus({ read: true })).toBe('read');
  });

  it('defaults to unread for a missing message', () => {
    expect(getInboxMessageStatus(null)).toBe('unread');
    expect(getInboxMessageStatus(undefined)).toBe('unread');
  });
});

describe('inboxMessageNeedsDecision', () => {
  it('requires approval with no decision', () => {
    expect(inboxMessageNeedsDecision({ requires_approval: true, decision: null })).toBe(true);
  });

  it('is false after a decision or without approval', () => {
    expect(inboxMessageNeedsDecision({ requires_approval: true, decision: 'approved' })).toBe(false);
    expect(inboxMessageNeedsDecision({ requires_approval: false })).toBe(false);
  });
});
