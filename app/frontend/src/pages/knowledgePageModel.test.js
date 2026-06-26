import { describe, expect, it } from 'vitest';
import {
  filterBases,
  formatBytes,
  formatCount,
  formatRelativeTime,
  groupBases,
  matchesFilter,
  matchesQuery,
  sortBases,
  statusMeta,
} from './knowledgePageModel';

const bases = [
  {
    id: 'arch',
    name: 'Progenitor architecture',
    status: 'connected',
    owner: 'Builder',
    embeddingModel: 'text-embedding-3-large',
    table: 'kb_architecture',
    documents: 320,
    lastSynced: '2026-06-27T06:00:00Z',
    tags: ['runtime', 'design'],
    collections: [{ name: 'graph_nodes' }],
    pluggedInto: ['Builder'],
  },
  {
    id: 'runbooks',
    name: 'Agent runbooks',
    status: 'syncing',
    owner: 'Observer',
    embeddingModel: 'text-embedding-3-small',
    table: 'kb_runbooks',
    documents: 88,
    lastSynced: '2026-06-26T06:00:00Z',
    tags: ['ops'],
  },
  {
    id: 'mcp',
    name: 'MCP reference',
    status: 'error',
    owner: 'Observer',
    embeddingModel: 'text-embedding-3-large',
    table: 'kb_mcp',
    documents: 154,
    lastSynced: '2026-06-20T06:00:00Z',
    tags: ['tooling'],
  },
];

describe('statusMeta', () => {
  it('maps known statuses and falls back to idle', () => {
    expect(statusMeta('connected').tone).toBe('ok');
    expect(statusMeta('error').label).toBe('Error');
    expect(statusMeta('nonsense').label).toBe('Idle');
  });
});

describe('matchesFilter', () => {
  it('passes all bases for All and matches by status label otherwise', () => {
    expect(matchesFilter(bases[0], 'All')).toBe(true);
    expect(matchesFilter(bases[0], 'Connected')).toBe(true);
    expect(matchesFilter(bases[0], 'Error')).toBe(false);
    expect(matchesFilter(bases[2], 'Error')).toBe(true);
  });
});

describe('matchesQuery', () => {
  it('searches across name, model, table, tags and collections', () => {
    expect(matchesQuery(bases[0], '')).toBe(true);
    expect(matchesQuery(bases[0], 'architecture')).toBe(true);
    expect(matchesQuery(bases[0], 'graph_nodes')).toBe(true);
    expect(matchesQuery(bases[0], 'text-embedding-3-large')).toBe(true);
    expect(matchesQuery(bases[0], 'ops')).toBe(false);
  });
});

describe('filterBases', () => {
  it('combines status filter and search', () => {
    expect(filterBases(bases, 'All', '').length).toBe(3);
    expect(filterBases(bases, 'Connected', '').map((b) => b.id)).toEqual(['arch']);
    expect(filterBases(bases, 'All', 'observer').map((b) => b.id)).toEqual(['runbooks', 'mcp']);
    expect(filterBases(null, 'All', '')).toEqual([]);
  });
});

describe('sortBases', () => {
  it('sorts by recent, name and documents', () => {
    expect(sortBases(bases, 'Recent').map((b) => b.id)).toEqual(['arch', 'runbooks', 'mcp']);
    expect(sortBases(bases, 'Name').map((b) => b.id)).toEqual(['runbooks', 'mcp', 'arch']);
    expect(sortBases(bases, 'Documents').map((b) => b.id)).toEqual(['arch', 'mcp', 'runbooks']);
  });
});

describe('groupBases', () => {
  it('buckets by status in order, omitting empty groups', () => {
    const groups = groupBases(bases, 'All', '', 'Recent');
    expect(groups.map((g) => g.key)).toEqual(['connected', 'syncing', 'error']);
    expect(groups[0].bases.map((b) => b.id)).toEqual(['arch']);
  });

  it('honours the active filter', () => {
    const groups = groupBases(bases, 'Error', '', 'Recent');
    expect(groups.map((g) => g.key)).toEqual(['error']);
  });
});

describe('formatBytes', () => {
  it('scales to human units', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(2048)).toBe('2 KB');
    expect(formatBytes(1572864)).toBe('1.5 MB');
  });
});

describe('formatCount', () => {
  it('groups thousands', () => {
    expect(formatCount(1500)).toBe('1,500');
    expect(formatCount('x')).toBe('0');
  });
});

describe('formatRelativeTime', () => {
  const now = new Date('2026-06-27T12:00:00Z').getTime();
  it('renders compact relative ages', () => {
    expect(formatRelativeTime(null, now)).toBe('--');
    expect(formatRelativeTime('2026-06-27T11:59:40Z', now)).toBe('now');
    expect(formatRelativeTime('2026-06-27T11:30:00Z', now)).toBe('30m');
    expect(formatRelativeTime('2026-06-27T09:00:00Z', now)).toBe('3h');
    expect(formatRelativeTime('2026-06-25T12:00:00Z', now)).toBe('2d');
  });
});
