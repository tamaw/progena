import { describe, it, expect } from 'vitest';
import {
  PINNED_CHAT_SESSIONS_KEY,
  readJson,
  writeJsonOrRemove,
  loadStoredPinnedSessionIds,
  persistPinnedSessionIds,
  loadQuickChatHandoff,
} from '@frontend/features/chat/models/chatStorage';

describe('readJson / writeJsonOrRemove', () => {
  it('returns the fallback when the key is missing', () => {
    expect(readJson('missing', 'fb')).toBe('fb');
  });
  it('parses stored JSON', () => {
    localStorage.setItem('k', JSON.stringify({ a: 1 }));
    expect(readJson('k', null)).toEqual({ a: 1 });
  });
  it('returns the fallback on malformed JSON', () => {
    localStorage.setItem('k', '{nope');
    expect(readJson('k', 'fb', 'oops')).toBe('fb');
  });
  it('removes the key when value is empty, writes otherwise', () => {
    writeJsonOrRemove('k', [1, 2], false);
    expect(JSON.parse(localStorage.getItem('k'))).toEqual([1, 2]);
    writeJsonOrRemove('k', [], true);
    expect(localStorage.getItem('k')).toBeNull();
  });
});

describe('pinned session ids', () => {
  it('round-trips through localStorage', () => {
    persistPinnedSessionIds(['a', 'b']);
    expect(localStorage.getItem(PINNED_CHAT_SESSIONS_KEY)).not.toBeNull();
    expect(loadStoredPinnedSessionIds()).toEqual(['a', 'b']);
  });
  it('removes the key when the list is empty', () => {
    persistPinnedSessionIds(['a']);
    persistPinnedSessionIds([]);
    expect(localStorage.getItem(PINNED_CHAT_SESSIONS_KEY)).toBeNull();
    expect(loadStoredPinnedSessionIds()).toEqual([]);
  });
  it('filters out non-string entries on load', () => {
    localStorage.setItem(PINNED_CHAT_SESSIONS_KEY, JSON.stringify(['a', 2, '', null, 'b']));
    expect(loadStoredPinnedSessionIds()).toEqual(['a', 'b']);
  });
});

describe('loadQuickChatHandoff', () => {
  it('returns a normalized handoff', () => {
    localStorage.setItem('handoff', JSON.stringify({ messages: [{ id: 'm' }], threadId: 's' }));
    expect(loadQuickChatHandoff('handoff')).toEqual({ messages: [{ id: 'm' }], threadId: 's' });
  });
  it('returns null for a missing or non-object payload', () => {
    expect(loadQuickChatHandoff('handoff')).toBeNull();
    localStorage.setItem('handoff', JSON.stringify('nope'));
    expect(loadQuickChatHandoff('handoff')).toBeNull();
  });
});
