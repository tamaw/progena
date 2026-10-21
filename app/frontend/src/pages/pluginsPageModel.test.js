import { describe, expect, it } from 'vitest';
import {
  filterPlugins,
  formatPluginVersion,
  matchesPluginFilter,
  matchesPluginQuery,
  pluginDetailStatus,
  pluginRowStatus,
  sortPlugins,
} from './pluginsPageModel';

describe('formatPluginVersion', () => {
  it('prefixes a real version with v', () => {
    expect(formatPluginVersion('1.2.3')).toBe('v1.2.3');
  });

  it('returns null for the "unknown" sentinel', () => {
    expect(formatPluginVersion('unknown')).toBeNull();
  });

  it('returns null for a missing version', () => {
    expect(formatPluginVersion(null)).toBeNull();
    expect(formatPluginVersion(undefined)).toBeNull();
    expect(formatPluginVersion('')).toBeNull();
  });
});

describe('pluginRowStatus', () => {
  it('is "ring" for a plugin with no install record', () => {
    expect(pluginRowStatus(null)).toBe('ring');
    expect(pluginRowStatus(undefined)).toBe('ring');
  });

  it('is "error" when the last install/reconcile failed', () => {
    expect(pluginRowStatus({ status: 'error' })).toBe('error');
  });

  it('is "pending" while an install is in flight', () => {
    expect(pluginRowStatus({ status: 'pending' })).toBe('pending');
  });

  it('is "update" when installed and an upstream change was detected', () => {
    expect(pluginRowStatus({ status: 'installed', available_version: '2.0.0' })).toBe('update');
  });

  it('is "installed" when installed, up to date, with no error', () => {
    expect(pluginRowStatus({ status: 'installed' })).toBe('installed');
  });

  it('prioritizes error over pending and update', () => {
    expect(pluginRowStatus({ status: 'error', available_version: '2.0.0' })).toBe('error');
  });
});

describe('pluginDetailStatus', () => {
  it('is muted "Not installed" with a hollow ring for no install record', () => {
    expect(pluginDetailStatus(null)).toEqual({ tone: 'muted', label: 'Not installed', ring: true });
  });

  it('is error "Install failed" for a failed install', () => {
    expect(pluginDetailStatus({ status: 'error' })).toEqual({ tone: 'error', label: 'Install failed', ring: false });
  });

  it('is warning "Installing…" while pending', () => {
    expect(pluginDetailStatus({ status: 'pending' })).toEqual({ tone: 'warning', label: 'Installing…', ring: false });
  });

  it('is ok "Update available" when an upstream change was detected', () => {
    expect(pluginDetailStatus({ status: 'installed', available_version: '2.0.0' }))
      .toEqual({ tone: 'ok', label: 'Update available', ring: false });
  });

  it('is muted "Installed" with a solid (non-ring) dot when up to date', () => {
    expect(pluginDetailStatus({ status: 'installed' })).toEqual({ tone: 'muted', label: 'Installed', ring: false });
  });

  it('never contradicts pluginRowStatus\'s precedence (error > pending > update > steady)', () => {
    const entries = [
      null,
      { status: 'error' },
      { status: 'pending' },
      { status: 'installed', available_version: '2.0.0' },
      { status: 'installed' },
    ];
    const expectedRowStatuses = ['ring', 'error', 'pending', 'update', 'installed'];
    entries.forEach((entry, index) => {
      expect(pluginRowStatus(entry)).toBe(expectedRowStatuses[index]);
      const detail = pluginDetailStatus(entry);
      // A hollow ring in the header should only ever occur for "not installed".
      expect(detail.ring).toBe(expectedRowStatuses[index] === 'ring');
    });
  });
});

describe('sortPlugins', () => {
  it('sorts alphabetically by displayName (falling back to name) in "Name" mode', () => {
    const plugins = [{ name: 'zeta' }, { displayName: 'Alpha Tools', name: 'alpha' }, { name: 'mu' }];
    expect(sortPlugins(plugins, 'Name').map((p) => p.displayName || p.name)).toEqual(['Alpha Tools', 'mu', 'zeta']);
  });

  it('leaves catalog order (already "recent") unchanged otherwise', () => {
    const plugins = [{ name: 'zeta' }, { name: 'alpha' }];
    expect(sortPlugins(plugins, 'Recent').map((p) => p.name)).toEqual(['zeta', 'alpha']);
  });

  it('returns an empty array for non-array input', () => {
    expect(sortPlugins(null, 'Name')).toEqual([]);
    expect(sortPlugins(undefined, 'Recent')).toEqual([]);
  });
});

describe('matchesPluginQuery', () => {
  const plugin = { name: 'hello-world', displayName: 'Hello World', description: 'A greeting skill' };

  it('matches on name, displayName, or description', () => {
    expect(matchesPluginQuery(plugin, 'hello')).toBe(true);
    expect(matchesPluginQuery(plugin, 'World')).toBe(true);
    expect(matchesPluginQuery(plugin, 'greeting')).toBe(true);
  });

  it('is case-insensitive', () => {
    expect(matchesPluginQuery(plugin, 'GREETING')).toBe(true);
  });

  it('returns true for an empty or whitespace-only query', () => {
    expect(matchesPluginQuery(plugin, '')).toBe(true);
    expect(matchesPluginQuery(plugin, '   ')).toBe(true);
    expect(matchesPluginQuery(plugin, undefined)).toBe(true);
  });

  it('returns false when nothing matches', () => {
    expect(matchesPluginQuery(plugin, 'nonexistent')).toBe(false);
  });
});

describe('matchesPluginFilter', () => {
  it('"All" (or any unrecognized value) matches everything', () => {
    expect(matchesPluginFilter({}, 'All', null)).toBe(true);
    expect(matchesPluginFilter({}, 'All', { status: 'installed' })).toBe(true);
  });

  it('"New" matches only plugins with no install record', () => {
    expect(matchesPluginFilter({}, 'New', null)).toBe(true);
    expect(matchesPluginFilter({}, 'New', { status: 'installed' })).toBe(false);
  });

  it('"Installed" matches any plugin with an install record', () => {
    expect(matchesPluginFilter({}, 'Installed', { status: 'installed' })).toBe(true);
    expect(matchesPluginFilter({}, 'Installed', { status: 'error' })).toBe(true);
    expect(matchesPluginFilter({}, 'Installed', null)).toBe(false);
  });

  it('"Available" matches only installed plugins with an available_version', () => {
    expect(matchesPluginFilter({}, 'Available', { status: 'installed', available_version: '2.0.0' })).toBe(true);
    expect(matchesPluginFilter({}, 'Available', { status: 'installed' })).toBe(false);
    expect(matchesPluginFilter({}, 'Available', null)).toBe(false);
  });
});

describe('filterPlugins', () => {
  const plugins = [
    { name: 'hello-world', description: 'greeting' },
    { name: 'build-tools', description: 'build things' },
    { name: 'build-workflows', description: 'build more things' },
  ];
  const installed = {
    'build-tools': { status: 'installed' },
    'build-workflows': { status: 'installed', available_version: '2.0.0' },
  };
  const getEntry = (plugin) => installed[plugin.name];

  it('combines filter and query', () => {
    expect(filterPlugins(plugins, { query: '', filter: 'All', getEntry }).map((p) => p.name))
      .toEqual(['hello-world', 'build-tools', 'build-workflows']);
    expect(filterPlugins(plugins, { query: '', filter: 'New', getEntry }).map((p) => p.name))
      .toEqual(['hello-world']);
    expect(filterPlugins(plugins, { query: '', filter: 'Installed', getEntry }).map((p) => p.name))
      .toEqual(['build-tools', 'build-workflows']);
    expect(filterPlugins(plugins, { query: '', filter: 'Available', getEntry }).map((p) => p.name))
      .toEqual(['build-workflows']);
    expect(filterPlugins(plugins, { query: 'build', filter: 'All', getEntry }).map((p) => p.name))
      .toEqual(['build-tools', 'build-workflows']);
  });

  it('returns an empty array for non-array input', () => {
    expect(filterPlugins(null, { query: '', filter: 'All' })).toEqual([]);
  });

  it('treats a missing getEntry as "not installed" for every plugin', () => {
    expect(filterPlugins(plugins, { query: '', filter: 'New' }).map((p) => p.name))
      .toEqual(['hello-world', 'build-tools', 'build-workflows']);
  });
});
