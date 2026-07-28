/**
 * Marketplaces + plugins domain: registered marketplace sources and installed
 * plugins, kept live by SSE snapshots and seeded via REST on page mount.
 */
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle } from './async';

export const initialState = {
  marketplaces: {
    items: [],
    loaded: false,
    loading: false,
    error: null,
    snapshotRequestId: null,
    requestId: null,
  },
  plugins: {
    items: {},
    loaded: false,
    loading: false,
    error: null,
    snapshotRequestId: null,
    requestId: null,
  },
  catalogByMarketplace: {},
};

function applyMarketplaces(state, payload, requestId = null) {
  const sources = payload?.sources || [];
  state.marketplaces.items = Array.isArray(sources) ? sources : [];
  state.marketplaces.loaded = true;
  state.marketplaces.snapshotRequestId = requestId;
  if (!state.marketplaces.loading || requestId === state.marketplaces.requestId) {
    state.marketplaces.loading = false;
    state.marketplaces.error = null;
    state.marketplaces.requestId = null;
  }
}

function applyPlugins(state, payload, requestId = null) {
  const plugins = payload?.plugins || {};
  state.plugins.items = plugins && typeof plugins === 'object' ? plugins : {};
  state.plugins.loaded = true;
  state.plugins.snapshotRequestId = requestId;
  if (!state.plugins.loading || requestId === state.plugins.requestId) {
    state.plugins.loading = false;
    state.plugins.error = null;
    state.plugins.requestId = null;
  }
}

// Load registered marketplace sources.
export const fetchMarketplaces = createAcknowledgedThunk('uiData/fetchMarketplaces', async ({ marketplacesUrl }, { requestId }) => {
  return requestAcknowledged(marketplacesUrl, { requestId });
});

// Persist the full marketplace source list.
export const saveMarketplaces = createAcknowledgedThunk('uiData/saveMarketplaces', async ({ marketplacesUrl, sources }, { requestId }) => {
  return requestAcknowledged(marketplacesUrl, {
    method: 'PUT',
    body: { sources },
    requestId,
  });
});

// Load the installed-plugins document.
export const fetchPlugins = createAcknowledgedThunk('uiData/fetchPlugins', async ({ pluginsUrl }, { requestId }) => {
  return requestAcknowledged(pluginsUrl, { requestId });
});

// Fetch one marketplace's catalog for browsing.
export const fetchMarketplaceCatalog = createAcknowledgedThunk('uiData/fetchMarketplaceCatalog', async ({ marketplacesUrl, name }, { requestId }) => {
  return requestAcknowledged(`${marketplacesUrl}/${encodeURIComponent(name)}/plugins`, { requestId });
});

// Install a plugin from a marketplace catalog.
export const installPlugin = createAcknowledgedThunk('uiData/installPlugin', async ({ pluginsUrl, marketplace, plugin }, { requestId }) => {
  return requestAcknowledged(pluginsUrl, {
    method: 'POST',
    body: { marketplace, plugin },
    requestId,
  });
});

// Uninstall a plugin.
export const uninstallPlugin = createAcknowledgedThunk('uiData/uninstallPlugin', async ({ pluginsUrl, marketplace, plugin }, { requestId }) => {
  return requestAcknowledged(`${pluginsUrl}/${encodeURIComponent(marketplace)}/${encodeURIComponent(plugin)}`, {
    method: 'DELETE',
    requestId,
  });
});

export const eventHandlers = {
  'marketplaces.snapshot': (state, payload) => {
    applyMarketplaces(state, payload, payload.request_id || null);
  },
  'plugins.snapshot': (state, payload) => {
    applyPlugins(state, payload, payload.request_id || null);
  },
  'plugin.catalog.snapshot': (state, payload) => {
    if (typeof payload?.name === 'string' && payload.name) {
      state.catalogByMarketplace[payload.name] = payload.catalog || {};
    }
  },
};

export function buildExtraReducers(builder) {
  addRequestLifecycle(builder, fetchMarketplaces, {
    select: (state) => state.marketplaces,
    errorMessage: 'Failed to load marketplaces',
  });
  addRequestLifecycle(builder, fetchPlugins, {
    select: (state) => state.plugins,
    errorMessage: 'Failed to load plugins',
  });
  return builder;
}

export const selectMarketplaces = (state) => state.uiData.marketplaces.items;
export const selectMarketplacesState = (state) => state.uiData.marketplaces;
export const selectPlugins = (state) => state.uiData.plugins.items;
export const selectPluginsState = (state) => state.uiData.plugins;
export const selectCatalogByMarketplace = (state) => state.uiData.catalogByMarketplace;
export const selectMarketplaceCatalog = (state, name) => (
  name ? state.uiData.catalogByMarketplace[name] || null : null
);
