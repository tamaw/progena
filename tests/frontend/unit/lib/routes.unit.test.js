import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useLocation, useNavigate } from 'react-router-dom';
import { slugifyTitle, buildEntityPath, useEntityUrlSync } from '@frontend/lib/routes';

vi.mock('react', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useEffect: vi.fn((effect) => effect()) };
});

vi.mock('react-router-dom', () => ({
  useLocation: vi.fn(),
  useNavigate: vi.fn(),
}));

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

describe('useEntityUrlSync hydration race', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('does not erase a deep-link detail path before the collection hydrates', () => {
    const navigate = vi.fn();
    useNavigate.mockReturnValue(navigate);
    useLocation.mockReturnValue({ pathname: '/chat/thread-42/old-title' });

    useEntityUrlSync('/chat', null, null);

    expect(navigate).not.toHaveBeenCalled();
  });

  it('hydrates a selected entity by syncing its canonical path once an id exists', () => {
    const navigate = vi.fn();
    useNavigate.mockReturnValue(navigate);
    useLocation.mockReturnValue({ pathname: '/chat', search: '' });

    useEntityUrlSync('/chat', 'thread-42', 'Loaded title');

    expect(navigate).toHaveBeenCalledWith({ pathname: '/chat/thread-42/loaded-title', search: '' }, { replace: true });
  });

  it('keeps the query string when fixing up the slug', () => {
    const navigate = vi.fn();
    useNavigate.mockReturnValue(navigate);
    useLocation.mockReturnValue({ pathname: '/knowledge/eng', search: '?page=reviewed%2Fa.md' });

    useEntityUrlSync('/knowledge', 'eng', 'Engineering');

    expect(navigate).toHaveBeenCalledWith(
      { pathname: '/knowledge/eng/engineering', search: '?page=reviewed%2Fa.md' },
      { replace: true },
    );
  });
});
