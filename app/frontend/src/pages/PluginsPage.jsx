import { usePaneCollapse } from '../components/layout/usePaneCollapse';
import { useEffect, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { selectRealtime } from '../store/realtimeSlice';
import { useConfig } from '../context/ConfigContext';
import {
  fetchMarketplaces,
  fetchPlugins,
  fetchMarketplaceCatalog,
  installPlugin,
  uninstallPlugin,
  selectMarketplaces,
  selectPlugins,
  selectMarketplaceCatalog,
} from '../store/uiDataSlice';
import DetailHeader, {
  DetailHeaderAction,
  DetailHeaderActions,
  DetailStatus,
  ReconnectActionIcon,
  SaveActionIcon,
} from '../components/layout/DetailHeader';
import {
  PaneCollapseSeam,
  PaneEmptyState,
  PaneExpandRail,
  PaneListFilters,
  PaneListSearch,
  PaneListToolbar,
  PaneListVirtualized,
  PaneSection,
} from '../components/layout/PaneList';
import Select from '../components/common/Select';

function PluginRow({ plugin, selected, onSelect }) {
  return (
    <button
      key={plugin.name}
      className={`prompts-tree-leaf plugins-tree-leaf ${selected ? 'active' : ''}`}
      onClick={() => onSelect(plugin)}
      type="button"
      title={plugin.name}
    >
      <span className="prompts-tree-file pane-row-title">{plugin.displayName || plugin.name}</span>
      <span className="prompts-tree-file-path pane-row-caption">{plugin.description || plugin.name}</span>
    </button>
  );
}

export default function PluginsPage() {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const marketplacesUrl = getApiUrl('marketplaces');
  const pluginsUrl = getApiUrl('plugins');
  const realtime = useSelector(selectRealtime);
  const marketplaces = useSelector(selectMarketplaces);
  const installedPlugins = useSelector(selectPlugins);
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse('plugins-sidebar prompts-sidebar sess-list');
  const [selectedMarketplace, setSelectedMarketplace] = useState('');
  const [selectedPlugin, setSelectedPlugin] = useState(null);
  const [query, setQuery] = useState('');
  const [activeFilter, setActiveFilter] = useState('All');
  const [error, setError] = useState(null);
  const [statusMessage, setStatusMessage] = useState('');

  const catalog = useSelector((state) => selectMarketplaceCatalog(state, selectedMarketplace));

  // Load registered marketplaces and installed plugins on mount.
  useEffect(() => {
    if (!realtime.connected) {
      return undefined;
    }
    let active = true;
    queueMicrotask(() => {
      if (active) {
        void dispatch(fetchMarketplaces({ marketplacesUrl })).unwrap().catch(() => {});
        void dispatch(fetchPlugins({ pluginsUrl })).unwrap().catch(() => {});
      }
    });
    return () => {
      active = false;
    };
  }, [dispatch, realtime.connected, marketplacesUrl, pluginsUrl]);

  // Default the marketplace picker to the first registered source once
  // marketplaces load, so the catalog has plugins to show immediately.
  useEffect(() => {
    if (selectedMarketplace || marketplaces.length === 0) {
      return undefined;
    }
    let active = true;
    queueMicrotask(() => {
      if (active) {
        setSelectedMarketplace(marketplaces[0].name);
      }
    });
    return () => {
      active = false;
    };
  }, [marketplaces, selectedMarketplace]);

  // Fetch the selected marketplace's catalog.
  useEffect(() => {
    if (!realtime.connected || !selectedMarketplace || catalog) {
      return;
    }
    dispatch(fetchMarketplaceCatalog({ marketplacesUrl, name: selectedMarketplace }))
      .unwrap()
      .catch((catalogError) => setError(catalogError));
  }, [realtime.connected, selectedMarketplace, catalog, dispatch, marketplacesUrl]);

  const marketplaceOptions = useMemo(
    () => marketplaces.map((m) => ({ value: m.name, label: m.name })),
    [marketplaces],
  );

  const visiblePlugins = useMemo(() => {
    if (!catalog || !catalog.plugins) {
      return [];
    }
    const normalizedQuery = query.trim().toLowerCase();
    const all = Array.isArray(catalog.plugins) ? catalog.plugins : [];
    return all.filter((plugin) => {
      const searchable = `${plugin.displayName || ''} ${plugin.name || ''} ${plugin.description || ''}`.toLowerCase();
      if (normalizedQuery && !searchable.includes(normalizedQuery)) {
        return false;
      }
      if (activeFilter === 'All') {
        return true;
      }
      const entry = installedPlugins[`${selectedMarketplace}/${plugin.name}`];
      if (activeFilter === 'New') {
        return !entry;
      }
      if (activeFilter === 'Installed') {
        return Boolean(entry);
      }
      if (activeFilter === 'Available') {
        return Boolean(entry?.available_version);
      }
      return true;
    });
  }, [catalog, query, activeFilter, installedPlugins, selectedMarketplace]);

  const pluginGroups = useMemo(() => {
    if (!visiblePlugins.length) {
      return [];
    }
    return [{ name: selectedMarketplace || 'Plugins', items: visiblePlugins }];
  }, [visiblePlugins, selectedMarketplace]);

  const isPluginInstalled = useMemo(() => {
    if (!selectedPlugin || !selectedMarketplace) {
      return false;
    }
    return Boolean(installedPlugins[`${selectedMarketplace}/${selectedPlugin.name}`]);
  }, [selectedPlugin, selectedMarketplace, installedPlugins]);

  const handleSelectPlugin = (plugin) => {
    setSelectedPlugin(plugin);
    setStatusMessage('');
  };

  const handleRefresh = async () => {
    if (!realtime.connected || !selectedMarketplace) {
      return;
    }
    setError(null);
    try {
      await dispatch(fetchMarketplaceCatalog({ marketplacesUrl, name: selectedMarketplace })).unwrap();
    } catch (refreshError) {
      setError(refreshError);
    }
  };

  const handleInstallPlugin = async () => {
    if (!selectedPlugin || !selectedMarketplace) {
      return;
    }
    if (!realtime.connected) {
      setError(new Error('Reconnect the realtime stream before installing a plugin.'));
      return;
    }
    const key = `${selectedMarketplace}/${selectedPlugin.name}`;
    if (installedPlugins[key]) {
      setStatusMessage(`Plugin ${key} is already installed.`);
      return;
    }
    setError(null);
    setStatusMessage('Installing plugin…');
    try {
      await dispatch(
        installPlugin({
          pluginsUrl,
          marketplace: selectedMarketplace,
          plugin: selectedPlugin.name,
        }),
      ).unwrap();
      setStatusMessage(`Plugin ${selectedPlugin.displayName || selectedPlugin.name} installed. Its skills will become active in a new conversation.`);
    } catch (installError) {
      setError(installError);
      setStatusMessage('');
    }
  };

  const handleUninstallPlugin = async () => {
    if (!selectedPlugin || !selectedMarketplace) {
      return;
    }
    if (!realtime.connected) {
      setError(new Error('Reconnect the realtime stream before uninstalling a plugin.'));
      return;
    }
    const key = `${selectedMarketplace}/${selectedPlugin.name}`;
    if (!installedPlugins[key]) {
      setStatusMessage(`Plugin ${key} is not installed.`);
      return;
    }
    setError(null);
    setStatusMessage('Uninstalling plugin…');
    try {
      await dispatch(
        uninstallPlugin({
          pluginsUrl,
          marketplace: selectedMarketplace,
          plugin: selectedPlugin.name,
        }),
      ).unwrap();
      setStatusMessage(`Plugin ${selectedPlugin.displayName || selectedPlugin.name} uninstalled.`);
    } catch (uninstallError) {
      setError(uninstallError);
      setStatusMessage('');
    }
  };

  return (
    <div className="plugins-page prompts-page">
      <PaneSection as="aside" className={paneClassName} aria-label="Plugins" onItemSelect={() => setCollapsed(true)}>
        {collapsed ? (
          <PaneExpandRail label="Plugins" onExpand={() => setCollapsed(false)} />
        ) : (
        <>
        <PaneCollapseSeam label="Plugins" onCollapse={() => setCollapsed(true)} />
        <PaneListFilters
          filters={['All', 'New', 'Installed', 'Available']}
          activeFilter={activeFilter}
          onFilterChange={setActiveFilter}
        />
        <PaneListToolbar>
          <PaneListSearch value={query} placeholder="Search plugins" onChange={setQuery} />
          {marketplaceOptions.length > 0 && (
            <div className="logs-source-select">
              <Select
                value={selectedMarketplace}
                onChange={(next) => setSelectedMarketplace(next)}
                options={marketplaceOptions}
                ariaLabel="Marketplace"
              />
            </div>
          )}
          <button type="button" className="sess-sort" onClick={handleRefresh} disabled={!realtime.connected || !selectedMarketplace}>
            Refresh
          </button>
        </PaneListToolbar>

        {!marketplaces.length && (
          <PaneEmptyState>No marketplaces registered.</PaneEmptyState>
        )}
        {marketplaces.length > 0 && !selectedMarketplace && (
          <PaneEmptyState>Select a marketplace to browse.</PaneEmptyState>
        )}
        {selectedMarketplace && !catalog && !error && (
          <PaneEmptyState>Loading marketplace catalog…</PaneEmptyState>
        )}
        {error && !catalog && (
          <PaneEmptyState>{error.message}</PaneEmptyState>
        )}
        {catalog && visiblePlugins.length === 0 && (
          <PaneEmptyState>No plugins match the current filters.</PaneEmptyState>
        )}
        {pluginGroups.length ? (
          <PaneListVirtualized
            className="prompts-sidebar-body"
            groups={pluginGroups}
            getItemKey={(plugin) => plugin.name}
            estimatedItemHeight={42}
            renderItem={(plugin) => (
              <PluginRow
                plugin={plugin}
                selected={selectedPlugin?.name === plugin.name}
                onSelect={handleSelectPlugin}
              />
            )}
          />
        ) : null}
        </>
        )}
      </PaneSection>

      <main className="plugins-editor prompts-editor">
        {!selectedPlugin && (
          <PaneEmptyState>Select a plugin to view details and install.</PaneEmptyState>
        )}

        {selectedPlugin && (
          <>
            <DetailHeader
              title={selectedPlugin.displayName || selectedPlugin.name}
              meta={[
                <DetailStatus key="status" tone={isPluginInstalled ? 'ok' : 'muted'}>
                  {isPluginInstalled ? 'Installed' : 'Not installed'}
                </DetailStatus>,
                selectedPlugin.version ? <span key="version" className="taskid">v{selectedPlugin.version}</span> : null,
                selectedPlugin.author ? <span key="author" className="taskid">{typeof selectedPlugin.author === 'string' ? selectedPlugin.author : selectedPlugin.author.name}</span> : null,
                selectedPlugin.license ? <span key="license" className="taskid">{selectedPlugin.license}</span> : null,
              ]}
              actions={(
                <DetailHeaderActions>
                  {isPluginInstalled ? (
                    <DetailHeaderAction
                      icon={<ReconnectActionIcon />}
                      label="Uninstall"
                      tooltip="Uninstall plugin"
                      ariaLabel="Uninstall plugin"
                      variant="danger"
                      onClick={handleUninstallPlugin}
                      disabled={!realtime.connected}
                    />
                  ) : (
                    <DetailHeaderAction
                      icon={<SaveActionIcon />}
                      label="Install"
                      tooltip="Install plugin"
                      ariaLabel="Install plugin"
                      variant="primary"
                      onClick={handleInstallPlugin}
                      disabled={!realtime.connected}
                    />
                  )}
                </DetailHeaderActions>
              )}
            />

            <div className="plugins-detail__body plugins-detail__body--preview">
              <div className="markdown-content">
                <p>{selectedPlugin.description || 'No description provided.'}</p>
                {selectedPlugin.homepage ? (
                  <p><a href={selectedPlugin.homepage} target="_blank" rel="noreferrer">{selectedPlugin.homepage}</a></p>
                ) : null}
                {selectedPlugin.skills && selectedPlugin.skills.length > 0 ? (
                  <>
                    <h4>Skills provided</h4>
                    <ul>
                      {selectedPlugin.skills.map((s) => (
                        <li key={s}>{s}</li>
                      ))}
                    </ul>
                  </>
                ) : null}
                {selectedPlugin.tags && selectedPlugin.tags.length > 0 ? (
                  <p>Tags: {selectedPlugin.tags.join(', ')}</p>
                ) : null}
              </div>
            </div>

            <div className="prompts-editor-footer">
              <span>{statusMessage || error?.message || (!realtime.connected ? 'Reconnect the realtime stream to install or uninstall plugins.' : '')}</span>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
