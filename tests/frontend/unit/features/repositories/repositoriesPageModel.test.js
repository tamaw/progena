import { describe, expect, it } from 'vitest';
import { repoStatus } from '@frontend/features/repositories/repositoriesPageModel';

const scan = (fields = {}) => ({
  exists: true, is_git_repo: true, dirty_count: 0, upstream_ahead: 0, upstream_behind: 0, ...fields,
});

describe('repoStatus', () => {
  it('treats a missing scan or checkout as not yet cloned (amber), not a scan in progress', () => {
    expect(repoStatus(null)).toEqual({ label: 'Not yet cloned', tone: 'warning' });
    expect(repoStatus(scan({ exists: false }))).toEqual({ label: 'Not yet cloned', tone: 'warning' });
  });

  it('shows a synced checkout as the quiet hollow ring', () => {
    expect(repoStatus(scan())).toEqual({ label: 'Synced', tone: 'idle' });
  });

  it('flags upstream changes green whether ahead or behind', () => {
    expect(repoStatus(scan({ upstream_behind: 2 })).tone).toBe('ok');
    expect(repoStatus(scan({ upstream_ahead: 1 })).label).toBe('Changes upstream');
  });

  it('reports a non-git path or scan error as a red scan error', () => {
    expect(repoStatus(scan({ is_git_repo: false }))).toEqual({ label: 'Scan error', tone: 'error' });
    expect(repoStatus(scan({ error: 'permission denied' })).tone).toBe('error');
  });

  it('ranks scan error over uncommitted changes, and uncommitted changes over upstream changes', () => {
    expect(repoStatus(scan({ error: 'boom', dirty_count: 3, upstream_behind: 1 })).label).toBe('Scan error');
    expect(repoStatus(scan({ dirty_count: 3, upstream_behind: 1 }))).toEqual({ label: 'Uncommitted changes', tone: 'warning' });
  });
});
