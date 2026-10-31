import { describe, expect, it } from 'vitest';
import {
  filterBases,
  formatCount,
  fromApiBase,
  groupBases,
  matchesFilter,
  matchesQuery,
  sortBases,
  statusMeta,
} from '@frontend/features/knowledge/knowledgePageModel';

const bases = [
  {
    id: 'arch',
    name: 'Progena architecture',
    status: 'indexed',
    owner: 'Builder',
    lastSynced: '2026-06-27T06:00:00Z',
    tags: ['runtime', 'design'],
  },
  {
    id: 'runbooks',
    name: 'Agent runbooks',
    status: 'indexing',
    owner: 'Observer',
    lastSynced: '2026-06-26T06:00:00Z',
    tags: ['ops'],
    indexedPages: 2,
    indexPages: 5,
  },
  {
    id: 'mcp',
    name: 'MCP reference',
    status: 'failed',
    owner: 'Observer',
    lastSynced: '2026-06-20T06:00:00Z',
    tags: ['tooling'],
    indexError: 'embedder not configured',
  },
];

describe('statusMeta', () => {
  it('maps statuses to tones and falls back to unknown', () => {
    expect(statusMeta('indexed').tone).toBe('idle');
    expect(statusMeta('indexing').tone).toBe('ok');
    expect(statusMeta('failed').tone).toBe('error');
    expect(statusMeta('nonsense')).toEqual({ label: 'Unknown', tone: 'warning' });
  });

  it('shows progress for indexing when counts are known', () => {
    expect(statusMeta('indexing', { indexedPages: 3, indexPages: 7 }).label).toBe('Indexing 3/7');
    expect(statusMeta('indexing', { indexedPages: 0, indexPages: 0 }).label).toBe('Indexing');
  });
});

describe('matchesFilter', () => {
  it('passes all bases for All and matches by status label otherwise', () => {
    expect(matchesFilter(bases[0], 'All')).toBe(true);
    expect(matchesFilter(bases[0], 'Indexed')).toBe(true);
    expect(matchesFilter(bases[0], 'Failed')).toBe(false);
    expect(matchesFilter(bases[2], 'Failed')).toBe(true);
    expect(matchesFilter(bases[1], 'Indexing')).toBe(true);
  });
});

describe('matchesQuery', () => {
  it('searches across name, description, owner, path and tags', () => {
    expect(matchesQuery(bases[0], '')).toBe(true);
    expect(matchesQuery(bases[0], 'architecture')).toBe(true);
    expect(matchesQuery(bases[0], 'Builder')).toBe(true);
    expect(matchesQuery(bases[0], 'ops')).toBe(false);
  });
});

describe('filterBases', () => {
  it('combines status filter and search', () => {
    expect(filterBases(bases, 'All', '').length).toBe(3);
    expect(filterBases(bases, 'Indexed', '').map((b) => b.id)).toEqual(['arch']);
    expect(filterBases(bases, 'All', 'observer').map((b) => b.id)).toEqual(['runbooks', 'mcp']);
    expect(filterBases(null, 'All', '')).toEqual([]);
  });
});

describe('sortBases', () => {
  it('sorts alphabetically by name', () => {
    expect(sortBases(bases).map((b) => b.id)).toEqual(['runbooks', 'mcp', 'arch']);
  });
});

describe('groupBases', () => {
  it('buckets by status in order, omitting empty groups', () => {
    const groups = groupBases(bases, 'All', '', 'Status');
    expect(groups.map((g) => g.key)).toEqual(['indexed', 'indexing', 'failed']);
    expect(groups[0].bases.map((b) => b.id)).toEqual(['arch']);
  });

  it('honours the active filter', () => {
    const groups = groupBases(bases, 'Failed', '', 'Status');
    expect(groups.map((g) => g.key)).toEqual(['failed']);
  });

  it('collapses to a single sorted list by name', () => {
    const groups = groupBases(bases, 'All', '', 'Name');
    expect(groups.map((g) => g.key)).toEqual(['all']);
    expect(groups[0].bases.map((b) => b.id)).toEqual(['runbooks', 'mcp', 'arch']);
  });
});

describe('formatCount', () => {
  it('groups thousands', () => {
    expect(formatCount(1500)).toBe('1,500');
    expect(formatCount('x')).toBe('0');
  });
});

describe('fromApiBase', () => {
  it('maps a backend row into the page shape', () => {
    const ui = fromApiBase({
      id: 'kb-1',
      name: 'Ops notes',
      description: 'runbooks',
      owner: 'observer',
      scope: 'Shared',
      index_status: 'indexed',
      page_count: 5,
      path: '/knowledge/kb-1/',
      tags: ['priority', 'ops'],
      updated_at: '2026-06-27T06:00:00Z',
    });
    expect(ui.id).toBe('kb-1');
    expect(ui.name).toBe('Ops notes');
    expect(ui.status).toBe('indexed');
    expect(ui.pageCount).toBe(5);
    expect(ui.path).toBe('/knowledge/kb-1/');
    expect(ui.tags).toEqual(['priority', 'ops']);
    expect(ui.lastSynced).toBe('2026-06-27T06:00:00Z');
  });

  it('falls back when optional fields are missing', () => {
    const ui = fromApiBase({ id: 'x' });
    expect(ui.name).toBe('x');
    expect(ui.status).toBe('unknown');
    expect(ui.pageCount).toBe(0);
    expect(ui.path).toBe('/knowledge/x/');
    expect(ui.tags).toEqual([]);
    expect(ui.indexError).toBe('');
  });

  it('maps index fields', () => {
    const ui = fromApiBase({
      id: 'y',
      index_status: 'failed',
      index_pages: 10,
      indexed_pages: 3,
      index_error: 'boom',
      indexed_at: '2026-10-01T00:00:00Z',
    });
    expect(ui.status).toBe('failed');
    expect(ui.indexPages).toBe(10);
    expect(ui.indexedPages).toBe(3);
    expect(ui.indexError).toBe('boom');
    expect(ui.indexedAt).toBe('2026-10-01T00:00:00Z');
  });
});
