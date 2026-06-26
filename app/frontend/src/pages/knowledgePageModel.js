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

export const KB_SORTS = ['Recent', 'Name', 'Documents'];

// Pane groups, in render order. Empty groups are dropped by groupBases().
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

export function sortBases(bases, sortMode) {
  const list = Array.isArray(bases) ? [...bases] : [];
  const byName = (a, b) => String(a.name || '').localeCompare(String(b.name || ''));
  if (sortMode === 'Name') {
    return list.sort(byName);
  }
  if (sortMode === 'Documents') {
    return list.sort((a, b) => (Number(b.documents) || 0) - (Number(a.documents) || 0) || byName(a, b));
  }
  // Recent (default): most-recently synced first.
  return list.sort((a, b) => {
    const at = new Date(a.lastSynced || 0).getTime() || 0;
    const bt = new Date(b.lastSynced || 0).getTime() || 0;
    return bt - at || byName(a, b);
  });
}

// Filter + search, then bucket by status into ordered, non-empty groups.
export function groupBases(bases, filter, query, sortMode) {
  const visible = filterBases(bases, filter, query);
  return KB_GROUP_ORDER
    .map(({ status, label }) => ({
      key: status,
      label,
      bases: sortBases(visible.filter((base) => base.status === status), sortMode),
    }))
    .filter((group) => group.bases.length > 0);
}

export function formatBytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) {
    return '0 B';
  }
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const exponent = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  const scaled = value / 1024 ** exponent;
  const rounded = scaled >= 100 || exponent === 0 ? Math.round(scaled) : Math.round(scaled * 10) / 10;
  return `${rounded} ${units[exponent]}`;
}

export function formatCount(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) {
    return '0';
  }
  return new Intl.NumberFormat().format(num);
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
