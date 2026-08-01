import { usePaneCollapse } from '../components/layout/usePaneCollapse';
import { useEffect, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { selectRealtime } from '../store/realtimeSlice';
import { useConfig } from '../context/ConfigContext';
import { renderMarkdownToHtml } from '../lib/markdown';
import { formatRelativeTime } from './tasksPageModel';
import {
  PLUGIN_FILTERS,
  PLUGIN_SORT_MODES,
  filterPlugins,
  formatPluginVersion,
  pluginDetailStatus,
  pluginRowStatus,
  sortPlugins,
} from './pluginsPageModel';
import {
  fetchMarketplaces,
  fetchPlugins,
  fetchMarketplaceCatalog,
  installPlugin,
  uninstallPlugin,
  fetchPluginSkillContent,
  selectMarketplaces,
  selectPlugins,
  selectMarketplaceCatalog,
  selectPluginSkillContent,
} from '../store/uiDataSlice';
import DetailHeader, {
  DetailHeaderAction,
  DetailHeaderActions,
  DetailStatus,
  InstallActionIcon,
  ReconnectActionIcon,
  UninstallActionIcon,
} from '../components/layout/DetailHeader';
import {
  PaneCollapseSeam,
  PaneEmptyState,
  PaneExpandRail,
  PaneListFilters,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
  PaneListVirtualized,
  PaneSection,
} from '../components/layout/PaneList';

function SkillIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="M4 3.5h6.5L12 5v7.5H4z" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.25" />
      <path d="M6 6.5h4M6 9h4" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.25" />
    </svg>
  );
}

function PluginRow({ plugin, entry, selected, onSelect }) {
  const status = pluginRowStatus(entry);
  return (
    <button
      key={plugin.name}
      className={`prompts-tree-leaf plugins-tree-leaf ${selected ? 'active' : ''}`}
      onClick={() => onSelect(plugin)}
      type="button"
      title={plugin.name}
    >
      <span className="prompts-tree-file pane-row-title">
        <span className={`plugins-status-dot plugins-status-dot--${status}`} />
        {plugin.displayName || plugin.name}
      </span>
      <span className="prompts-tree-file-path pane-row-caption">{plugin.description || plugin.name}</span>
    </button>
  );
}

export default function PluginsPage() {
  const dispatch = useDispatch();
  const navigate = useNavigate();
  const location = useLocation();
  const { marketplace: urlMarketplace, plugin: urlPluginName } = useParams();
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
  const [sort, setSort] = useState('Recent');
  const [error, setError] = useState(null);
  const [statusMessage, setStatusMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [needsNewConversation, setNeedsNewConversation] = useState(false);
  const [previewSkill, setPreviewSkill] = useState(null);

  const catalog = useSelector((state) => selectMarketplaceCatalog(state, selectedMarketplace));
  const skillPreview = useSelector((state) => (
    selectPluginSkillContent(state, selectedMarketplace, selectedPlugin?.name, previewSkill)
  ));
  const [skillPreviewLoading, setSkillPreviewLoading] = useState(false);
  const [skillPreviewError, setSkillPreviewError] = useState(null);

  // Load marketplaces and installed plugins on mount.
  useEffect(() => {
    if (!realtime.connected) {
      return undefined;
    }
    let active = true;
    queueMicrotask(() => {
      if (active) {
        void dispatch(fetchMarketplaces({ marketplacesUrl })).unwrap().catch((fetchError) => setError(fetchError));
        void dispatch(fetchPlugins({ pluginsUrl })).unwrap().catch((fetchError) => setError(fetchError));
      }
    });
    return () => {
      active = false;
    };
  }, [dispatch, realtime.connected, marketplacesUrl, pluginsUrl]);

  // Default to the marketplace named in the URL if it's actually registered,
  // otherwise fall back to the first registered marketplace.
  useEffect(() => {
    if (selectedMarketplace || !Object.keys(marketplaces).length) {
      return undefined;
    }
    let active = true;
    queueMicrotask(() => {
      if (active) {
        const initial = urlMarketplace && marketplaces[urlMarketplace]
          ? urlMarketplace
          : Object.keys(marketplaces)[0];
        setSelectedMarketplace(initial);
      }
    });
    return () => {
      active = false;
    };
  }, [marketplaces, selectedMarketplace, urlMarketplace]);

  // Revalidate the selected marketplace's catalog whenever it becomes selected
  // (mount, marketplace switch, or reconnect) -- always asks the backend, which
  // serves its own 5-minute cache when fresh and only re-fetches from GitHub
  // once that's expired, so this is cheap in the common case while still
  // catching upstream changes without the user having to click Update first.
  // Keyed on the marketplace name (not `catalog`) so a snapshot arriving
  // mid-flight doesn't re-trigger this same effect.
  useEffect(() => {
    if (!realtime.connected || !selectedMarketplace) {
      return undefined;
    }
    let active = true;
    queueMicrotask(() => {
      if (!active) {
        return;
      }
      setLoading(true);
      dispatch(fetchMarketplaceCatalog({ marketplacesUrl, name: selectedMarketplace }))
        .unwrap()
        .catch((catalogError) => {
          if (active) setError(catalogError);
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    });
    return () => {
      active = false;
    };
  }, [realtime.connected, selectedMarketplace, dispatch, marketplacesUrl]);

  // Fetch a skill's SKILL.md body on demand when its preview is opened.
  useEffect(() => {
    if (!realtime.connected || !previewSkill || !selectedPlugin || !selectedMarketplace || skillPreview) {
      return undefined;
    }
    let active = true;
    queueMicrotask(() => {
      if (!active) {
        return;
      }
      setSkillPreviewLoading(true);
      dispatch(fetchPluginSkillContent({
        pluginsUrl,
        marketplace: selectedMarketplace,
        plugin: selectedPlugin.name,
        skill: previewSkill,
      }))
        .unwrap()
        .catch((previewError) => {
          if (active) setSkillPreviewError(previewError);
        })
        .finally(() => {
          if (active) setSkillPreviewLoading(false);
        });
    });
    return () => {
      active = false;
    };
  }, [realtime.connected, previewSkill, selectedPlugin, selectedMarketplace, skillPreview, dispatch, pluginsUrl]);

  const marketplaceOptions = useMemo(
    () => Object.keys(marketplaces).map((name) => ({ value: name, label: name })),
    [marketplaces],
  );

  const visiblePlugins = useMemo(() => {
    if (!catalog || !catalog.plugins) {
      return [];
    }
    const filtered = filterPlugins(catalog.plugins, {
      query,
      filter: activeFilter,
      getEntry: (plugin) => installedPlugins[`${selectedMarketplace}/${plugin.name}`],
    });
    return sortPlugins(filtered, sort);
  }, [catalog, query, activeFilter, installedPlugins, selectedMarketplace, sort]);

  // Auto-select the plugin named in the URL if it's in the current list,
  // otherwise the top plugin, once the list has something to show -- so the
  // detail pane isn't empty on first load. Only fires when nothing is
  // selected yet -- never overrides an existing selection (e.g. after a
  // filter/search narrows the list).
  useEffect(() => {
    if (selectedPlugin || !visiblePlugins.length) {
      return undefined;
    }
    let active = true;
    queueMicrotask(() => {
      if (!active) {
        return;
      }
      const matched = urlPluginName
        ? visiblePlugins.find((plugin) => plugin.name === urlPluginName)
        : null;
      setSelectedPlugin(matched || visiblePlugins[0]);
      setStatusMessage('');
      setError(null);
      setPreviewSkill(null);
      setSkillPreviewError(null);
    });
    return () => {
      active = false;
    };
  }, [visiblePlugins, selectedPlugin, urlPluginName]);

  // Keep the URL in sync with whichever marketplace/plugin is actually
  // selected -- mirrors useEntityUrlSync (lib/routes.js), but for the two
  // path segments this page needs instead of one flat id. Always replaces
  // (never pushes) so selecting plugins doesn't pollute browser history.
  useEffect(() => {
    if (!selectedMarketplace) {
      return;
    }
    const nextPath = selectedPlugin
      ? `/plugins/${encodeURIComponent(selectedMarketplace)}/${encodeURIComponent(selectedPlugin.name)}`
      : `/plugins/${encodeURIComponent(selectedMarketplace)}`;
    if (nextPath !== location.pathname) {
      navigate(nextPath, { replace: true });
    }
  }, [selectedMarketplace, selectedPlugin, navigate, location.pathname]);

  const installedEntry = useMemo(() => {
    if (!selectedPlugin || !selectedMarketplace) {
      return null;
    }
    return installedPlugins[`${selectedMarketplace}/${selectedPlugin.name}`] || null;
  }, [selectedPlugin, selectedMarketplace, installedPlugins]);

  const isPluginInstalled = Boolean(installedEntry);

  const pluginSourceLabel = useMemo(() => {
    const source = selectedPlugin?.source;
    if (typeof source === 'string') {
      return source;
    }
    if (source && typeof source === 'object') {
      if (source.source === 'git-subdir') {
        return `${source.url}/${source.path}${source.ref ? `@${source.ref}` : ''}`;
      }
      if (source.source === 'github') {
        return `${source.repo}${source.ref ? `@${source.ref}` : ''}`;
      }
      if (source.url) {
        return source.url;
      }
    }
    return null;
  }, [selectedPlugin]);

  const handleSelectPlugin = (plugin) => {
    setSelectedPlugin(plugin);
    setStatusMessage('');
    setError(null);
    setPreviewSkill(null);
    setSkillPreviewError(null);
  };

  const handleTogglePreviewSkill = (skill) => {
    setPreviewSkill((current) => (current === skill ? null : skill));
    setSkillPreviewError(null);
  };

  const performInstall = async ({ isUpdate = false } = {}) => {
    if (!selectedPlugin || !selectedMarketplace) {
      return;
    }
    if (!realtime.connected) {
      setError(new Error(`Reconnect the realtime stream before ${isUpdate ? 'updating' : 'installing'} a plugin.`));
      return;
    }
    setError(null);
    setStatusMessage(isUpdate ? 'Updating plugin…' : 'Installing plugin…');
    try {
      await dispatch(
        installPlugin({
          pluginsUrl,
          marketplace: selectedMarketplace,
          plugin: selectedPlugin.name,
        }),
      ).unwrap();
      const label = selectedPlugin.displayName || selectedPlugin.name;
      setStatusMessage(isUpdate ? `Plugin ${label} updated.` : `Plugin ${label} installed.`);
      setNeedsNewConversation(true);
    } catch (installError) {
      setError(installError);
      setStatusMessage('');
    }
  };

  const handleInstallPlugin = () => {
    const key = `${selectedMarketplace}/${selectedPlugin?.name}`;
    if (selectedPlugin && installedPlugins[key]) {
      setStatusMessage(`Plugin ${key} is already installed.`);
      return;
    }
    void performInstall();
  };

  const handleUpdatePlugin = () => {
    void performInstall({ isUpdate: true });
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

  const pluginTree = useMemo(() => {
    if (!visiblePlugins.length) {
      return [];
    }
    return [{
      name: selectedMarketplace || 'Plugins',
      skills: visiblePlugins,
    }];
  }, [visiblePlugins, selectedMarketplace]);

  return (
    <div className="plugins-page">
      {needsNewConversation ? (
        <div className="plugins-page__banner" role="status">
          <span>A plugin install or update just finished. Its skills will only be available starting in a new conversation.</span>
          <button type="button" onClick={() => setNeedsNewConversation(false)} aria-label="Dismiss">×</button>
        </div>
      ) : null}
      <div className="plugins-page__row prompts-page">
      <PaneSection as="aside" className={paneClassName} aria-label="Plugins" onItemSelect={() => setCollapsed(true)}>
        {collapsed ? (
          <PaneExpandRail label="Plugins" onExpand={() => setCollapsed(false)} />
        ) : (
        <>
        <PaneCollapseSeam label="Plugins" onCollapse={() => setCollapsed(true)} />
        <PaneListFilters
          filters={PLUGIN_FILTERS}
          activeFilter={activeFilter}
          onFilterChange={setActiveFilter}
        />
        <PaneListToolbar>
          <PaneListSearch value={query} placeholder="Search plugins" onChange={setQuery} />
          <PaneListSortButton
            title="Sort plugins"
            options={PLUGIN_SORT_MODES}
            value={sort}
            onSelect={setSort}
          />
        </PaneListToolbar>

        {!realtime.connected && (
          <PaneEmptyState>Reconnect the realtime stream to load plugins.</PaneEmptyState>
        )}
        {realtime.connected && !marketplaceOptions.length && (
          <PaneEmptyState>No marketplaces registered. Add one in Settings → Marketplaces.</PaneEmptyState>
        )}
        {realtime.connected && marketplaceOptions.length > 0 && !selectedMarketplace && (
          <PaneEmptyState>Select a marketplace to browse its plugins.</PaneEmptyState>
        )}
        {realtime.connected && selectedMarketplace && loading && !catalog && (
          <PaneEmptyState>Loading marketplace catalog…</PaneEmptyState>
        )}
        {realtime.connected && selectedMarketplace && error && !catalog && (
          <PaneEmptyState>{error.message}</PaneEmptyState>
        )}
        {realtime.connected && selectedMarketplace && catalog && visiblePlugins.length === 0 && (
          <PaneEmptyState>No plugins match the current filters.</PaneEmptyState>
        )}
        {pluginTree.length ? (
          <PaneListVirtualized
            className="prompts-sidebar-body"
            groups={pluginTree.map((group) => ({ ...group, items: group.skills }))}
            getItemKey={(plugin) => plugin.name}
            estimatedItemHeight={42}
            renderItem={(plugin) => (
              <PluginRow
                plugin={plugin}
                entry={installedPlugins[`${selectedMarketplace}/${plugin.name}`]}
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
                <DetailStatus
                  key="status"
                  tone={pluginDetailStatus(installedEntry).tone}
                  className={pluginDetailStatus(installedEntry).ring ? 'plugins-detail__status--ring' : ''}
                >
                  {pluginDetailStatus(installedEntry).label}
                </DetailStatus>,
                formatPluginVersion(installedEntry?.available_version)
                  ? <span key="available" className="plugins-update-chip">{formatPluginVersion(installedEntry.available_version)}</span>
                  : null,
                selectedMarketplace ? <span key="marketplace" className="taskid">{selectedMarketplace}</span> : null,
                formatPluginVersion(installedEntry?.installed_version || selectedPlugin.version)
                  ? <span key="version" className="taskid">{formatPluginVersion(installedEntry?.installed_version || selectedPlugin.version)}</span>
                  : null,
                installedEntry?.installed_at
                  ? <span key="installed-at" className="taskid">Installed {formatRelativeTime(installedEntry.installed_at)} ago</span>
                  : null,
                selectedPlugin.author ? <span key="author" className="taskid">{typeof selectedPlugin.author === 'string' ? selectedPlugin.author : selectedPlugin.author.name}</span> : null,
                selectedPlugin.license ? <span key="license" className="taskid">{selectedPlugin.license}</span> : null,
              ]}
              actions={(
                <DetailHeaderActions menuLabel="Plugin actions">
                  {isPluginInstalled && installedEntry?.available_version ? (
                    <DetailHeaderAction
                      icon={<ReconnectActionIcon />}
                      label="Update"
                      tooltip={formatPluginVersion(installedEntry.available_version) ? `Update to ${formatPluginVersion(installedEntry.available_version)}` : 'Update available'}
                      ariaLabel="Update plugin"
                      variant="primary"
                      onClick={handleUpdatePlugin}
                      disabled={!realtime.connected}
                    />
                  ) : null}
                  {isPluginInstalled ? (
                    <DetailHeaderAction
                      icon={<UninstallActionIcon />}
                      label="Uninstall"
                      tooltip="Uninstall plugin"
                      ariaLabel="Uninstall plugin"
                      onClick={handleUninstallPlugin}
                      disabled={!realtime.connected}
                    />
                  ) : (
                    <DetailHeaderAction
                      icon={<InstallActionIcon />}
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
              {installedEntry?.status === 'error' && installedEntry.status_message ? (
                <PaneEmptyState>{installedEntry.status_message}</PaneEmptyState>
              ) : null}

              <p className="plugins-detail__description">{selectedPlugin.description || 'No description provided.'}</p>

              <section className="tools-section">
                <div className="tools-section__head">
                  <h2>Details</h2>
                </div>
                <dl className="tools-connection-grid">
                  <div><dt>Marketplace</dt><dd>{selectedMarketplace || '—'}</dd></div>
                  <div><dt>Source</dt><dd title={pluginSourceLabel || ''}>{pluginSourceLabel || '—'}</dd></div>
                  <div><dt>Version</dt><dd>{formatPluginVersion(installedEntry?.installed_version || selectedPlugin.version) || '—'}</dd></div>
                  {selectedPlugin.homepage ? (
                    <div><dt>Homepage</dt><dd><a href={selectedPlugin.homepage} target="_blank" rel="noreferrer">{selectedPlugin.homepage}</a></dd></div>
                  ) : null}
                  {selectedPlugin.tags && selectedPlugin.tags.length > 0 ? (
                    <div><dt>Tags</dt><dd>{selectedPlugin.tags.join(', ')}</dd></div>
                  ) : null}
                  {installedEntry?.installed_at ? (
                    <div><dt>Installed</dt><dd title={installedEntry.installed_at}>{formatRelativeTime(installedEntry.installed_at)} ago</dd></div>
                  ) : null}
                </dl>
              </section>

              <section className="tools-section">
                <div className="tools-section__head">
                  <h2>
                    Skills provided
                    {installedEntry?.skills?.length ? <span>{installedEntry.skills.length}</span> : null}
                  </h2>
                </div>
                <div className="tools-tool-list">
                  {installedEntry?.skills && installedEntry.skills.length > 0 ? (
                    installedEntry.skills.map((skill) => (
                      <button
                        type="button"
                        className={`tools-tool-row plugins-skill-row ${previewSkill === skill ? 'is-active' : ''}`}
                        key={skill}
                        onClick={() => handleTogglePreviewSkill(skill)}
                      >
                        <span className="tools-tool-row__icon"><SkillIcon /></span>
                        <div className="tools-tool-row__body">
                          <strong>{skill}</strong>
                          <p>Installed skill from {selectedPlugin.displayName || selectedPlugin.name}</p>
                        </div>
                      </button>
                    ))
                  ) : (
                    <PaneEmptyState>
                      {isPluginInstalled
                        ? 'No skills reported for this install.'
                        : 'Install the plugin to see which skills it provides.'}
                    </PaneEmptyState>
                  )}
                </div>

                {previewSkill ? (
                  <div className="plugins-skill-preview">
                    {skillPreviewLoading && !skillPreview ? (
                      <PaneEmptyState>Loading {previewSkill}…</PaneEmptyState>
                    ) : skillPreviewError && !skillPreview ? (
                      <PaneEmptyState>{skillPreviewError.message}</PaneEmptyState>
                    ) : skillPreview ? (
                      <div
                        className="markdown-content plugins-skill-preview__body"
                        dangerouslySetInnerHTML={{ __html: renderMarkdownToHtml(skillPreview.content) }}
                      />
                    ) : null}
                  </div>
                ) : null}
              </section>
            </div>

            <div className="prompts-editor-footer">
              <span>{statusMessage || error?.message || (!realtime.connected ? 'Reconnect the realtime stream to install or uninstall plugins.' : '')}</span>
            </div>
          </>
        )}
      </main>
      </div>
    </div>
  );
}
