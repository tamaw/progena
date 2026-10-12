// Pure helpers for the Knowledge tab: Postgres (pgvector) knowledge bases that
// can be plugged into Progenitor agents. Kept free of React so the
// filter/search/sort/group logic is unit-testable in isolation, mirroring
// tasksPageModel.js.

export const KB_STATUS_META = {
  connected: { label: 'Connected', tone: 'ok' },
  syncing: { label: 'Syncing', tone: 'warning' },
  error: { label: 'Error', tone: 'error' },
  idle: { label: 'Idle', tone: 'muted' },
};

// Left-pane filter chips. Mirrors the Agents page (All / Running / ...): one
// "All" plus one chip per actionable status.
export const KB_FILTERS = ['All', 'Connected', 'Syncing', 'Error'];

// Group/sort dimensions for the left pane.
export const KB_SORTS = ['Status', 'Type', 'Name'];

// Status groups, in render order. Empty groups are dropped by groupBases().
const KB_GROUP_ORDER = [
  { status: 'connected', label: 'Connected' },
  { status: 'syncing', label: 'Syncing' },
  { status: 'error', label: 'Needs attention' },
  { status: 'idle', label: 'Idle' },
];

export function statusMeta(status) {
  return KB_STATUS_META[status] || KB_STATUS_META.idle;
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

function typeOf(base) {
  return base.storeType || 'pgvector';
}

// Filter + search, then bucket into ordered, non-empty groups by the chosen
// dimension: Status (default), Type (e.g. pgvector), or Name (single list).
export function groupBases(bases, filter, query, sortMode) {
  const visible = filterBases(bases, filter, query);
  if (!visible.length) {
    return [];
  }
  if (sortMode === 'Type') {
    const types = [...new Set(visible.map(typeOf))].sort((a, b) => a.localeCompare(b));
    return types.map((type) => ({
      key: `type:${type}`,
      label: type,
      bases: sortBases(visible.filter((base) => typeOf(base) === type)),
    }));
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
  const lastSynced = apiBase.updated_at || apiBase.created_at || null;
  return {
    id: apiBase.id,
    name: apiBase.name || apiBase.id,
    status: apiBase.status || 'connected',
    owner: apiBase.owner || 'user',
    description: apiBase.description || 'Knowledge base.',
    instructions: typeof apiBase.instructions === 'string' ? apiBase.instructions : '',
    lastSynced,
    pageCount: Number(apiBase.page_count) || 0,
    path: apiBase.path || `/knowledge/${apiBase.id}/`,
    tags: Array.isArray(apiBase.tags) ? apiBase.tags : [],
  };
}

export function formatRelativeTime(value, now = Date.now()) {
  if (!value) {
    return '--';
  }
  const parsed = new Date(value).getTime();
  if (!Number.isFinite(parsed)) {
    return '--';
  }
  const diff = Math.max(0, now - parsed);
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'now';
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  return new Date(parsed).toLocaleDateString([], { month: 'short', day: 'numeric' });
}
