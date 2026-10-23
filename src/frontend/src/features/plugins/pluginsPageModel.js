// Pure helpers for the Plugins page, kept free of React so they're
// unit-testable in isolation, mirroring inboxPageModel.js / tasksPageModel.js /
// knowledgePageModel.js.

export const PLUGIN_SORT_MODES = ['Recent', 'Name'];

export const PLUGIN_FILTERS = [
  { value: 'All', label: 'All' },
  { value: 'Available', label: 'Update' },
  { value: 'New', label: 'Available' },
  { value: 'Installed', label: 'Installed' },
];

// The backend falls back to the literal string "unknown" when a plugin has no
// declared version and no pinned ref/sha to fall back to (see
// resolve_plugin_version in src/progena/plugins/sources.py) -- render that
// as "no version" rather than the confusing "vunknown".
export function formatPluginVersion(version) {
  if (!version || version === 'unknown') {
    return null;
  }
  return `v${version}`;
}

// A plugin row's status dot. 'ring' (hollow, muted) means "not installed" --
// the only state with a hollow dot. 'installed' is a solid gray dot: a
// steady state with nothing to act on, but visually present the same way an
// error/pending/update dot is (just neutral-colored).
export function pluginRowStatus(entry) {
  if (!entry) {
    return 'ring';
  }
  if (entry.status === 'error') {
    return 'error';
  }
  if (entry.status === 'pending') {
    return 'pending';
  }
  if (entry.available_version) {
    return 'update';
  }
  return 'installed';
}

// Detail-header status badge tone/label, sharing the exact same status
// precedence as pluginRowStatus (error > pending > update > steady state) so
// the header dot never contradicts the row dot. Only "not installed" gets
// the hollow ring (tone "idle"); "installed, up to date" gets a solid muted
// dot. Green is reserved for "something needs your attention" (an update),
// not for "installed and fine."
export function pluginDetailStatus(entry) {
  if (!entry) {
    return { tone: 'idle', label: 'Not installed' };
  }
  if (entry.status === 'error') {
    return { tone: 'error', label: 'Install failed' };
  }
  if (entry.status === 'pending') {
    return { tone: 'warning', label: 'Installing…' };
  }
  if (entry.available_version) {
    return { tone: 'ok', label: 'Update available' };
  }
  return { tone: 'muted', label: 'Installed' };
}

export function sortPlugins(plugins, sort) {
  const list = Array.isArray(plugins) ? plugins : [];
  if (sort === 'Name') {
    return [...list].sort((a, b) => (
      String(a.displayName || a.name || '').localeCompare(String(b.displayName || b.name || ''))
    ));
  }
  // Catalog order is already the marketplace's own "recent" ordering.
  return list;
}

export function matchesPluginQuery(plugin, query) {
  const needle = String(query || '').trim().toLowerCase();
  if (!needle) {
    return true;
  }
  const searchable = `${plugin.displayName || ''} ${plugin.name || ''} ${plugin.description || ''}`.toLowerCase();
  return searchable.includes(needle);
}

// `entry` is the installed-plugins record for this plugin (or null/undefined
// if it isn't installed) -- callers resolve it from `installedPlugins` since
// that lookup needs the current marketplace, which this module doesn't know.
export function matchesPluginFilter(plugin, filter, entry) {
  if (filter === 'New') {
    return !entry;
  }
  if (filter === 'Installed') {
    return Boolean(entry);
  }
  if (filter === 'Available') {
    return Boolean(entry?.available_version);
  }
  return true;
}

export function filterPlugins(plugins, { query, filter, getEntry } = {}) {
  const list = Array.isArray(plugins) ? plugins : [];
  return list.filter((plugin) => (
    matchesPluginQuery(plugin, query)
    && matchesPluginFilter(plugin, filter, getEntry ? getEntry(plugin) : null)
  ));
}
