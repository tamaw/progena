import { describe, it, expect } from 'vitest';
import { slugifyTitle, buildEntityPath } from './routes';

describe('slugifyTitle', () => {
  it('lowercases and hyphenates', () => {
    expect(slugifyTitle('My Chat Session')).toBe('my-chat-session');
  });
  it('strips punctuation and collapses runs of non-alphanumerics', () => {
    expect(slugifyTitle('Fix: the "flaky" test!!')).toBe('fix-the-flaky-test');
  });
  it('trims leading/trailing hyphens', () => {
    expect(slugifyTitle('  --weird--  ')).toBe('weird');
  });
  it('caps at the max length without a dangling hyphen', () => {
    const long = 'word '.repeat(30).trim();
    const slug = slugifyTitle(long);
    expect(slug.length).toBeLessThanOrEqual(60);
    expect(slug.endsWith('-')).toBe(false);
  });
  it('respects a custom max length', () => {
    expect(slugifyTitle('abcdefghij', 5)).toBe('abcde');
  });
  it('returns an empty string for blank or non-slug-able input', () => {
    expect(slugifyTitle('')).toBe('');
    expect(slugifyTitle(null)).toBe('');
    expect(slugifyTitle('!!!')).toBe('');
  });
});

describe('buildEntityPath', () => {
  it('returns the bare base path when there is no id', () => {
    expect(buildEntityPath('/chat', null, 'anything')).toBe('/chat');
    expect(buildEntityPath('/inbox', '', 'anything')).toBe('/inbox');
  });
  it('builds :base/:id/:slug for a titled entity', () => {
    expect(buildEntityPath('/chat', 'thread_sess_1234', 'My Chat Session')).toBe('/chat/thread_sess_1234/my-chat-session');
    expect(buildEntityPath('/inbox', 'msg_42', 'Weekly Digest')).toBe('/inbox/msg_42/weekly-digest');
  });
  it('drops the slug segment when the title has no sluggable characters', () => {
    expect(buildEntityPath('/inbox', 'msg_42', '')).toBe('/inbox/msg_42');
    expect(buildEntityPath('/inbox', 'msg_42', '💬')).toBe('/inbox/msg_42');
  });
  it('encodes ids that need it', () => {
    expect(buildEntityPath('/inbox', 'weird/id here', 'Title')).toBe('/inbox/weird%2Fid%20here/title');
  });
});
