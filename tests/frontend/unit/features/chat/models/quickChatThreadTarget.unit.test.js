import { describe, expect, it } from 'vitest';
import {
  createQuickChatThreadTarget,
  normalizeQuickChatThreadId,
  resolveQuickChatThreadAdoption,
} from '@frontend/features/chat/models/quickChatThreadTarget';

describe('normalizeQuickChatThreadId', () => {
  it('accepts canonical uuid and namespaced thread ids', () => {
    expect(normalizeQuickChatThreadId('3f2c9a1e-8b4d-4c1a-9f00-1234567890ab')).toBe('3f2c9a1e-8b4d-4c1a-9f00-1234567890ab');
    expect(normalizeQuickChatThreadId('  routine:abc.1  ')).toBe('routine:abc.1');
  });

  it('rejects missing, blank, oversized or unsafe values', () => {
    expect(normalizeQuickChatThreadId(undefined)).toBeNull();
    expect(normalizeQuickChatThreadId(42)).toBeNull();
    expect(normalizeQuickChatThreadId('   ')).toBeNull();
    expect(normalizeQuickChatThreadId('a'.repeat(201))).toBeNull();
    expect(normalizeQuickChatThreadId('../etc/passwd')).toBeNull();
    expect(normalizeQuickChatThreadId('thread id')).toBeNull();
  });
});

describe('createQuickChatThreadTarget', () => {
  it('stamps each valid request with a distinct id', () => {
    const first = createQuickChatThreadTarget({ threadId: 'thread-a' });
    const second = createQuickChatThreadTarget({ threadId: 'thread-a' });
    expect(first.threadId).toBe('thread-a');
    expect(second.threadId).toBe('thread-a');
    expect(first.id).not.toBe(second.id);
  });

  it('returns null for an invalid target', () => {
    expect(createQuickChatThreadTarget({ threadId: '' })).toBeNull();
    expect(createQuickChatThreadTarget(null)).toBeNull();
  });
});

describe('resolveQuickChatThreadAdoption', () => {
  const target = { id: 't1', threadId: 'thread-a' };

  it('ignores a missing or already adopted target', () => {
    expect(resolveQuickChatThreadAdoption({ target: null })).toBe('ignore');
    expect(resolveQuickChatThreadAdoption({ target, adoptedTargetId: 't1', currentThreadId: 'thread-b' })).toBe('ignore');
  });

  it('refreshes when the panel already holds the same thread, even mid-reply', () => {
    expect(resolveQuickChatThreadAdoption({ target, currentThreadId: 'thread-a', ownStreamActive: true })).toBe('refresh');
  });

  it('never replaces an in-flight reply on another thread', () => {
    expect(resolveQuickChatThreadAdoption({ target, currentThreadId: 'scratch', ownStreamActive: true })).toBe('busy');
  });

  it('adopts a different thread when idle', () => {
    expect(resolveQuickChatThreadAdoption({ target, currentThreadId: 'scratch', ownStreamActive: false })).toBe('adopt');
    expect(resolveQuickChatThreadAdoption({ target, currentThreadId: null })).toBe('adopt');
  });
});
