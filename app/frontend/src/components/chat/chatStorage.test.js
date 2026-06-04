import { describe, it, expect, vi } from 'vitest';
import {
  PINNED_CHAT_SESSIONS_KEY,
  readJson,
  writeJsonOrRemove,
  loadStoredPinnedSessionIds,
  persistPinnedSessionIds,
  loadStoredCustomChatTitle,
  persistCustomChatTitle,
  loadQuickChatHandoff,
} from './chatStorage';

describe('readJson / writeJsonOrRemove', () => {
  it('returns the fallback when the key is missing', () => {
    expect(readJson('missing', 'fb')).toBe('fb');
  });
  it('parses stored JSON', () => {
    localStorage.setItem('k', JSON.stringify({ a: 1 }));
    expect(readJson('k', null)).toEqual({ a: 1 });
  });
  it('returns the fallback (and warns) on malformed JSON', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    localStorage.setItem('k', '{nope');
    expect(readJson('k', 'fb', 'oops')).toBe('fb');
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
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

describe('custom chat title', () => {
  it('persists a normalized title and reads it back', () => {
    persistCustomChatTitle('sess1', '  My Title  ');
    expect(loadStoredCustomChatTitle()).toEqual({ sessionId: 'sess1', title: 'My Title' });
  });
  it('removes the key when the title is empty', () => {
    persistCustomChatTitle('sess1', 'X');
    persistCustomChatTitle('sess1', '   ');
    expect(loadStoredCustomChatTitle()).toEqual({ sessionId: null, title: '' });
  });
});

describe('loadQuickChatHandoff', () => {
  it('returns a normalized handoff', () => {
    localStorage.setItem('handoff', JSON.stringify({ messages: [{ id: 'm' }], sessionId: 's' }));
    expect(loadQuickChatHandoff('handoff')).toEqual({ messages: [{ id: 'm' }], sessionId: 's' });
  });
  it('returns null for a missing or non-object payload', () => {
    expect(loadQuickChatHandoff('handoff')).toBeNull();
    localStorage.setItem('handoff', JSON.stringify('nope'));
    expect(loadQuickChatHandoff('handoff')).toBeNull();
  });
});
