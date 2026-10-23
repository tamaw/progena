// Pure helpers for the Knowledge tab: agent-maintained markdown knowledge bases.
// Kept free of React so the filter/search/sort/group logic is unit-testable
// in isolation, mirroring tasksPageModel.js.

export const KB_STATUS_META = {
  indexed: { label: 'Indexed', tone: 'ok' },
  indexing: { label: 'Indexing', tone: 'warning' },
  failed: { label: 'Failed', tone: 'error' },
};

// Left-pane filter chips. Mirrors the Agents page (All / Running / ...): one
// "All" plus one chip per actionable status.
export const KB_FILTERS = ['All', 'Indexed', 'Indexing', 'Failed'];

// Group/sort dimensions for the left pane.
export const KB_SORTS = ['Status', 'Name'];

// Status groups, in render order. Empty groups are dropped by groupBases().
const KB_GROUP_ORDER = [
  { status: 'indexed', label: 'Indexed' },
  { status: 'indexing', label: 'Indexing' },
  { status: 'failed', label: 'Needs attention' },
];

export function statusMeta(status, { indexedPages, indexPages } = {}) {
  const meta = KB_STATUS_META[status] || KB_STATUS_META.indexing;
  if (status === 'indexing' && Number.isFinite(indexedPages) && Number.isFinite(indexPages) && indexPages > 0) {
    return { ...meta, label: `Indexing ${indexedPages}/${indexPages}` };
  }
  return meta;
}

export function matchesFilter(base, filter) {
  if (!filter || filter === 'All') {
    return true;
  }
  return statusMeta(base.status).label.toLowerCase() === filter.toLowerCase();
}

export function matchesQuery(base, query) {
  const normalized = String(query || '').trim().toLowerCase();
  if (!normalized) {
    return true;
  }
  const haystack = [
    base.name,
    base.description,
    base.owner,
    base.path,
    ...(Array.isArray(base.tags) ? base.tags : []),
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
  return haystack.includes(normalized);
}

export function filterBases(bases, filter, query) {
  if (!Array.isArray(bases)) {
    return [];
  }
  return bases.filter((base) => matchesFilter(base, filter) && matchesQuery(base, query));
}

// Alphabetical by name — the within-group ordering for every dimension.
export function sortBases(bases) {
  return [...(Array.isArray(bases) ? bases : [])].sort(
    (a, b) => String(a.name || '').localeCompare(String(b.name || '')),
  );
}

// Filter + search, then bucket into ordered, non-empty groups by the chosen
// dimension: Status (default) or Name (single list).
export function groupBases(bases, filter, query, sortMode) {
  const visible = filterBases(bases, filter, query);
  if (!visible.length) {
    return [];
  }
  if (sortMode === 'Name') {
    return [{ key: 'all', label: 'All', bases: sortBases(visible) }];
  }
  // Status (default).
  return KB_GROUP_ORDER
    .map(({ status, label }) => ({
      key: status,
      label,
      bases: sortBases(visible.filter((base) => base.status === status)),
    }))
    .filter((group) => group.bases.length > 0);
}

export function formatCount(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) {
    return '0';
  }
  return new Intl.NumberFormat().format(num);
}

// Adapt a backend knowledge-base row (snake_case) into the shape the page
// renders. `path` and `page_count` are derived by the backend snapshot; saves
// PUT the raw rows, so fields not mapped here are preserved.
export function fromApiBase(apiBase) {
  return {
    id: apiBase.id,
    name: apiBase.name || apiBase.id,
    status: apiBase.index_status || 'indexing',
    owner: apiBase.owner || 'user',
    description: apiBase.description || 'Knowledge base.',
    pageCount: Number(apiBase.page_count) || 0,
    path: apiBase.path || `/knowledge/${apiBase.id}/`,
    tags: Array.isArray(apiBase.tags) ? apiBase.tags : [],
    indexPages: Number(apiBase.index_pages) || 0,
    indexedPages: Number(apiBase.indexed_pages) || 0,
    indexError: apiBase.index_error || '',
    indexedAt: apiBase.indexed_at || null,
    lastSynced: apiBase.indexed_at || apiBase.updated_at || apiBase.created_at || null,
  };
}
