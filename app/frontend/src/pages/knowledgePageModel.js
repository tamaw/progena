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
    base.embeddingModel,
    base.table,
    base.database,
    base.indexType,
    base.metric,
    ...(Array.isArray(base.tags) ? base.tags : []),
    ...(Array.isArray(base.collections) ? base.collections.map((c) => c.name) : []),
    ...(Array.isArray(base.pluggedInto) ? base.pluggedInto : []),
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

// Adapt a backend knowledge-base row (snake_case, pgvector-oriented) into the
// shape the page renders. Every knowledge base is reachable by the runtime
// `knowledge_search` tool, so each one reports as plugged into Progenitor. The
// raw `sources` array is passed through unchanged so edits can PUT it back.
export function fromApiBase(apiBase) {
  const collections = Array.isArray(apiBase.collections) ? apiBase.collections : [];
  const documents = Number(apiBase.documents) || 0;
  const lastSynced = apiBase.updated_at || apiBase.created_at || null;
  const host = '127.0.0.1:15433';
  const database = 'progenitor';
  return {
    id: apiBase.id,
    name: apiBase.name || apiBase.id,
    status: apiBase.status || 'connected',
    owner: apiBase.owner || 'user',
    description: apiBase.description || 'pgvector knowledge base.',
    embeddingModel: apiBase.embedding_model || 'progenitor-hash-256',
    storeType: apiBase.store_type || 'pgvector',
    dimensions: Number(apiBase.dimensions) || 256,
    indexType: apiBase.index_type || 'Flat',
    metric: apiBase.metric || 'cosine',
    host,
    database,
    table: 'knowledge_documents',
    connectionString: apiBase.connection_string || `postgresql://progenitor@${host}/${database}`,
    documents,
    lastSynced,
    tags: Array.isArray(apiBase.tags) ? apiBase.tags : [],
    sources: Array.isArray(apiBase.sources) ? apiBase.sources : [],
    collections: collections.map((name) => ({ name, updated: lastSynced })),
    pluggedInto: ['Progenitor'],
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
