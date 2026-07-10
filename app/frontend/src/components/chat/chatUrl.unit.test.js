import { describe, it, expect } from 'vitest';
import { slugifyChatTitle, buildChatPath } from './chatUrl';

describe('slugifyChatTitle', () => {
  it('lowercases and hyphenates', () => {
    expect(slugifyChatTitle('My Chat Session')).toBe('my-chat-session');
  });
  it('strips punctuation and collapses runs of non-alphanumerics', () => {
    expect(slugifyChatTitle('Fix: the "flaky" test!!')).toBe('fix-the-flaky-test');
  });
  it('trims leading/trailing hyphens', () => {
    expect(slugifyChatTitle('  --weird--  ')).toBe('weird');
  });
  it('caps at the max length without a dangling hyphen', () => {
    const long = 'word '.repeat(30).trim();
    const slug = slugifyChatTitle(long);
    expect(slug.length).toBeLessThanOrEqual(60);
    expect(slug.endsWith('-')).toBe(false);
  });
  it('respects a custom max length', () => {
    expect(slugifyChatTitle('abcdefghij', 5)).toBe('abcde');
  });
  it('returns an empty string for blank or non-slug-able input', () => {
    expect(slugifyChatTitle('')).toBe('');
    expect(slugifyChatTitle(null)).toBe('');
    expect(slugifyChatTitle('!!!')).toBe('');
  });
});

describe('buildChatPath', () => {
  it('builds /chat when there is no thread id', () => {
    expect(buildChatPath(null, 'anything')).toBe('/chat');
    expect(buildChatPath('', 'anything')).toBe('/chat');
  });
  it('builds /chat/:id/:slug for a titled thread', () => {
    expect(buildChatPath('thread_sess_1234', 'My Chat Session')).toBe('/chat/thread_sess_1234/my-chat-session');
  });
  it('builds /chat/:id (no slug segment) when the title has no sluggable characters', () => {
    expect(buildChatPath('thread_sess_1234', '')).toBe('/chat/thread_sess_1234');
    expect(buildChatPath('thread_sess_1234', '💬')).toBe('/chat/thread_sess_1234');
  });
  it('encodes ids that need it', () => {
    expect(buildChatPath('thread/weird id', 'Title')).toBe('/chat/thread%2Fweird%20id/title');
  });
});
