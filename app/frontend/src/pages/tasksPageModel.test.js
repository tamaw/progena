import { describe, expect, it } from 'vitest';

import {
  buildCardBadges,
  cardTags,
  formatAgentName,
  formatRelativeTime,
  formatTaskLabel,
  getCardAssignee,
  getCardService,
  groupVisibleCards,
  isArchivedCard,
  normalizePriority,
  priorityLabel,
  sortCards,
  statusMeta,
} from './tasksPageModel';

describe('tasksPageModel', () => {
  it('formats task labels consistently', () => {
    expect(formatTaskLabel('builder_progenitor')).toBe('Builder Progenitor');
    expect(formatTaskLabel('review:queue')).toBe('Review Queue');
  });

  it('returns known and fallback status metadata', () => {
    expect(statusMeta('draft')).toEqual({ label: 'Draft', tone: 'muted' });
    expect(statusMeta('complete')).toEqual({ label: 'Complete', tone: 'ok' });
    expect(statusMeta('queued_review')).toEqual({ label: 'Queued Review', tone: 'muted' });
  });

  it('aliases formatAgentName to the shared label formatter', () => {
    expect(formatAgentName('observe_agent')).toBe('Observe Agent');
  });
});

describe('normalizePriority', () => {
  it('treats failures, blocks, and errors as high priority', () => {
    expect(normalizePriority({ status: 'failed' })).toBe('high');
    expect(normalizePriority({ status: 'blocked' })).toBe('high');
    expect(normalizePriority({ last_error: 'boom' })).toBe('high');
  });

  it('treats running cards as medium and everything else as low', () => {
    expect(normalizePriority({ status: 'running' })).toBe('medium');
    expect(normalizePriority({ status: 'idle' })).toBe('low');
    expect(normalizePriority({})).toBe('low');
  });
});

describe('priorityLabel', () => {
  it('maps priority keys to display labels', () => {
    expect(priorityLabel('high')).toBe('High');
    expect(priorityLabel('medium')).toBe('Medium');
    expect(priorityLabel('low')).toBe('Low');
    expect(priorityLabel('anything-else')).toBe('Low');
  });
});

describe('isArchivedCard', () => {
  it('detects archived metadata', () => {
    expect(isArchivedCard({ metadata: { archived_at: '2024-01-01' } })).toBe(true);
    expect(isArchivedCard({ metadata: {} })).toBe(false);
    expect(isArchivedCard(null)).toBe(false);
  });
});

describe('cardTags', () => {
  it('normalizes labels to non-empty strings', () => {
    expect(cardTags({ labels: ['a', '', null, 1] })).toEqual(['a', '1']);
    expect(cardTags({})).toEqual([]);
    expect(cardTags(null)).toEqual([]);
  });
});

describe('getCardService', () => {
  it('prefers the last two spec path segments', () => {
    expect(getCardService({ spec_path: 'one/two/three/four.md' })).toBe('three/four.md');
    expect(getCardService({ spec_path: 'alpha/beta' })).toBe('alpha/beta');
  });

  it('falls back to branch name then owner then default', () => {
    expect(getCardService({ branch_name: 'feature/x' })).toBe('feature/x');
    expect(getCardService({ owner: 'me' })).toBe('me');
    expect(getCardService({})).toBe('Todo Service');
  });
});

describe('getCardAssignee', () => {
  it('derives name, initial, and a stable color by index', () => {
    const assignee = getCardAssignee({ agent_name: 'builder_progenitor' }, 0);
    expect(assignee.name).toBe('Builder Progenitor');
    expect(assignee.initial).toBe('B');
    expect(assignee.color).toBe('#2f6ff6');
  });

  it('cycles through the color palette', () => {
    expect(getCardAssignee({ owner: 'a' }, 5).color).toBe('#2f6ff6');
  });
});

describe('buildCardBadges', () => {
  it('builds badges only for present fields', () => {
    expect(buildCardBadges({ spec_path: 's', pr_url: 'p' })).toEqual([
      { key: 'spec', label: 'Spec' },
      { key: 'pr', label: 'PR' },
    ]);
    expect(buildCardBadges({ branch_name: 'main', last_error: 'x' })).toEqual([
      { key: 'branch', label: 'main' },
      { key: 'error', label: 'Blocked', tone: 'red' },
    ]);
    expect(buildCardBadges({})).toEqual([]);
  });
});

describe('sortCards', () => {
  const cards = [
    { title: 'Beta', status: 'idle', updated_at: '2024-01-01' },
    { title: 'Alpha', status: 'failed', updated_at: '2024-03-01' },
    { title: 'Gamma', status: 'running', updated_at: '2024-02-01' },
  ];

  it('sorts by name', () => {
    const sorted = [...cards].sort(sortCards('Name')).map((c) => c.title);
    expect(sorted).toEqual(['Alpha', 'Beta', 'Gamma']);
  });

  it('sorts by most recent update', () => {
    const sorted = [...cards].sort(sortCards('Recent')).map((c) => c.title);
    expect(sorted).toEqual(['Alpha', 'Gamma', 'Beta']);
  });

  it('sorts by derived priority then recency', () => {
    const sorted = [...cards].sort(sortCards('Priority')).map((c) => c.title);
    expect(sorted).toEqual(['Alpha', 'Gamma', 'Beta']);
  });
});

describe('groupVisibleCards', () => {
  const board = {
    columns: [
      { id: 'backlog', name: 'Backlog' },
      { id: 'done', name: 'Done' },
    ],
    cards: [
      { id: '1', column_id: 'backlog', title: 'Wire stream', owner: 'user', status: 'idle', updated_at: '2024-01-02' },
      { id: '2', column_id: 'backlog', title: 'Archived', owner: 'user', status: 'idle', metadata: { archived_at: 'x' } },
      { id: '3', column_id: 'done', title: 'Ship it', owner: 'agent', status: 'complete', labels: ['review'], updated_at: '2024-01-01' },
    ],
  };

  it('returns empty for a missing board', () => {
    expect(groupVisibleCards(null, 'all', '', 'Priority')).toEqual([]);
  });

  it('groups by column and excludes archived cards', () => {
    const groups = groupVisibleCards(board, 'all', '', 'Priority');
    expect(groups.map((g) => g.id)).toEqual(['backlog', 'done']);
    expect(groups[0].cards.map((c) => c.id)).toEqual(['1']);
    expect(groups[1].cards.map((c) => c.id)).toEqual(['3']);
  });

  it('filters to my tasks', () => {
    const groups = groupVisibleCards(board, 'mine', '', 'Priority');
    expect(groups[1].cards).toHaveLength(0);
  });

  it('filters watched cards by label', () => {
    const groups = groupVisibleCards(board, 'watching', '', 'Priority');
    expect(groups[0].cards).toHaveLength(0);
    expect(groups[1].cards.map((c) => c.id)).toEqual(['3']);
  });

  it('matches the search query against card text', () => {
    const groups = groupVisibleCards(board, 'all', 'ship', 'Priority');
    expect(groups[0].cards).toHaveLength(0);
    expect(groups[1].cards.map((c) => c.id)).toEqual(['3']);
  });
});

describe('formatRelativeTime', () => {
  it('returns a placeholder for missing or invalid input', () => {
    expect(formatRelativeTime('')).toBe('--');
    expect(formatRelativeTime('not-a-date')).toBe('--');
  });

  it('formats recent timestamps in compact units', () => {
    const now = Date.now();
    expect(formatRelativeTime(new Date(now - 30 * 1000).toISOString())).toBe('now');
    expect(formatRelativeTime(new Date(now - 5 * 60 * 1000).toISOString())).toBe('5m');
    expect(formatRelativeTime(new Date(now - 3 * 60 * 60 * 1000).toISOString())).toBe('3h');
    expect(formatRelativeTime(new Date(now - 2 * 24 * 60 * 60 * 1000).toISOString())).toBe('2d');
  });
});
