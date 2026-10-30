import { UI_EVENT } from '../../realtime/eventVocabulary';
/**
 * Marketplaces + plugins domain: registered marketplace sources and installed
 * plugins, kept live by SSE snapshots and seeded via REST on page mount.
 */
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle, addKeyedRequestLifecycle, completeKeyedRequest, selectKeyedRequest } from '../../store/async';

export const initialState = {
  marketplaces: {
    items: {},
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
  skillContentByKey: {},
  catalogRequestByMarketplace: {},
  skillContentRequestByKey: {},
};

function applyMarketplaces(state, payload, requestId = null) {
  const marketplaces = payload?.marketplaces || {};
  state.marketplaces.items = marketplaces && typeof marketplaces === 'object' ? marketplaces : {};
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

// Persist the full marketplaces document.
export const saveMarketplaces = createAcknowledgedThunk('uiData/saveMarketplaces', async ({ marketplacesUrl, marketplaces }, { requestId }) => {
  return requestAcknowledged(marketplacesUrl, {
    method: 'PUT',
    body: { marketplaces },
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

// Fetch one installed plugin skill's SKILL.md body for preview.
export const fetchPluginSkillContent = createAcknowledgedThunk('uiData/fetchPluginSkillContent', async ({ pluginsUrl, marketplace, plugin, skill }, { requestId }) => {
  const acknowledgement = await requestAcknowledged(
    `${pluginsUrl}/${encodeURIComponent(marketplace)}/${encodeURIComponent(plugin)}/skills/${encodeURIComponent(skill)}`,
    { requestId },
  );
  return { ...acknowledgement, marketplace, plugin, skill };
});

export const eventHandlers = {
  [UI_EVENT.MARKETPLACES_SNAPSHOT]: (state, payload) => {
    applyMarketplaces(state, payload, payload.request_id || null);
  },
  [UI_EVENT.PLUGINS_SNAPSHOT]: (state, payload) => {
    applyPlugins(state, payload, payload.request_id || null);
  },
  [UI_EVENT.PLUGIN_CATALOG_SNAPSHOT]: (state, payload) => {
    if (typeof payload?.name === 'string' && payload.name) {
      state.catalogByMarketplace[payload.name] = payload.catalog || {};
      completeKeyedRequest(state.catalogRequestByMarketplace, payload.name, payload.request_id);
    }
  },
  [UI_EVENT.PLUGIN_SKILL_CONTENT_UPDATED]: (state, payload) => {
    if (typeof payload?.marketplace === 'string' && typeof payload?.plugin === 'string' && typeof payload?.skill === 'string') {
      const key = `${payload.marketplace}/${payload.plugin}/${payload.skill}`;
      state.skillContentByKey[key] = payload;
      completeKeyedRequest(state.skillContentRequestByKey, key, payload.request_id);
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
  addKeyedRequestLifecycle(builder, fetchMarketplaceCatalog, {
    selectRequests: (state) => state.catalogRequestByMarketplace,
    keyFromArgs: ({ name } = {}) => name,
    errorMessage: 'Failed to load marketplace catalog',
  });
  return addKeyedRequestLifecycle(builder, fetchPluginSkillContent, {
    selectRequests: (state) => state.skillContentRequestByKey,
    keyFromArgs: ({ marketplace, plugin, skill } = {}) => (
      marketplace && plugin && skill ? `${marketplace}/${plugin}/${skill}` : null
    ),
    errorMessage: 'Failed to load plugin skill content',
  });
}

export const selectMarketplaces = (state) => state.uiData.marketplaces.items;
export const selectMarketplacesState = (state) => state.uiData.marketplaces;
export const selectPlugins = (state) => state.uiData.plugins.items;
export const selectPluginsState = (state) => state.uiData.plugins;
export const selectCatalogByMarketplace = (state) => state.uiData.catalogByMarketplace;
export const selectMarketplaceCatalog = (state, name) => (
  name ? state.uiData.catalogByMarketplace[name] || null : null
);
export const selectPluginSkillContent = (state, marketplace, plugin, skill) => (
  marketplace && plugin && skill
    ? state.uiData.skillContentByKey[`${marketplace}/${plugin}/${skill}`] || null
    : null
);
export const selectMarketplaceCatalogRequest = (state, name) => (
  selectKeyedRequest(state.uiData.catalogRequestByMarketplace, name)
);
export const selectPluginSkillContentRequest = (state, marketplace, plugin, skill) => (
  selectKeyedRequest(
    state.uiData.skillContentRequestByKey,
    marketplace && plugin && skill ? `${marketplace}/${plugin}/${skill}` : null,
  )
);
