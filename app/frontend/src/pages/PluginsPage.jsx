import { usePaneCollapse } from '../components/layout/usePaneCollapse';
import { useEffect, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { selectRealtime } from '../store/realtimeSlice';
import { useConfig } from '../context/ConfigContext';
import MarkdownContent from '../components/common/MarkdownContent';
import InlineFailure from '../components/common/InlineFailure';
import useUiAction from '../hooks/useUiAction';
import { formatCompactRelativeAge } from '../lib/displayTime';
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
  selectMarketplacesState,
  selectPlugins,
  selectPluginsState,
  selectMarketplaceCatalog,
  selectMarketplaceCatalogRequest,
  selectPluginSkillContent,
  selectPluginSkillContentRequest,
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
  PaneListRows,
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
      className={`plugins-tree-leaf ${selected ? 'active' : ''}`}
      onClick={() => onSelect(plugin)}
      type="button"
      title={plugin.name}
    >
      <span className="plugins-tree-file pane-row-title">
        <span className={`plugins-status-dot plugins-status-dot--${status}`} />
        {plugin.displayName || plugin.name}
      </span>
      <span className="plugins-tree-file-path pane-row-caption">{plugin.description || plugin.name}</span>
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
  const { collapsed, setCollapsed, paneClassName } = usePaneCollapse('plugins-sidebar sess-list');
  const [selectedMarketplace, setSelectedMarketplace] = useState('');
  const [selectedPlugin, setSelectedPlugin] = useState(null);
  const [query, setQuery] = useState('');
  const [activeFilter, setActiveFilter] = useState('All');
  const [sort, setSort] = useState('Recent');
  const [statusMessage, setStatusMessage] = useState('');
  const [awaitingPluginChange, setAwaitingPluginChange] = useState(null);
  const [dismissedPluginChangeId, setDismissedPluginChangeId] = useState(null);
  const [previewSkill, setPreviewSkill] = useState(null);
  const [marketplaceSkillsByKey, setMarketplaceSkillsByKey] = useState({});
  const [marketplaceSkillsRetry, setMarketplaceSkillsRetry] = useState(0);

  const marketplacesState = useSelector(selectMarketplacesState);
  const pluginsState = useSelector(selectPluginsState);
  const catalog = useSelector((state) => selectMarketplaceCatalog(state, selectedMarketplace));
  const catalogRequest = useSelector((state) => selectMarketplaceCatalogRequest(state, selectedMarketplace));
  const skillPreview = useSelector((state) => (
    selectPluginSkillContent(state, selectedMarketplace, selectedPlugin?.name, previewSkill)
  ));
  const skillPreviewRequest = useSelector((state) => (
    selectPluginSkillContentRequest(state, selectedMarketplace, selectedPlugin?.name, previewSkill)
  ));
  const pluginAction = useUiAction(`plugin-action:${selectedMarketplace}/${selectedPlugin?.name || ''}`);
  const initialFailure = marketplacesState.error || pluginsState.error;

  // Seed the two stream-backed documents when a live connection is available.
  useEffect(() => {
    if (!realtime.connected) return undefined;
    dispatch(fetchMarketplaces({ marketplacesUrl }));
    dispatch(fetchPlugins({ pluginsUrl }));
    return undefined;
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

  // Each selected catalog tracks its own causal request lifecycle. A 202 only
  // starts that lifecycle; the matching stream snapshot finishes it.
  useEffect(() => {
    if (!realtime.connected || !selectedMarketplace) return undefined;
    dispatch(fetchMarketplaceCatalog({ marketplacesUrl, name: selectedMarketplace }));
    return undefined;
  }, [realtime.connected, selectedMarketplace, dispatch, marketplacesUrl]);

  // Discover skills from the marketplace source before installation.
  useEffect(() => {
    if (!realtime.connected || !selectedMarketplace || !selectedPlugin) return undefined;
    let active = true;
    const pluginKey = `${selectedMarketplace}/${selectedPlugin.name}`;
    fetch(`${pluginsUrl}/${encodeURIComponent(selectedMarketplace)}/${encodeURIComponent(selectedPlugin.name)}/preview`)
      .then((response) => {
        if (!response.ok) throw new Error(`Unable to preview plugin skills (${response.status})`);
        return response.json();
      })
      .then((payload) => {
        if (!active) return;
        setMarketplaceSkillsByKey((current) => ({
          ...current,
          [pluginKey]: { skills: Array.isArray(payload.skills) ? payload.skills : [], error: null },
        }));
      })
      .catch((previewError) => {
        if (!active) return;
        setMarketplaceSkillsByKey((current) => ({
          ...current,
          [pluginKey]: { skills: [], error: previewError },
        }));
      });
    return () => { active = false; };
  }, [realtime.connected, selectedMarketplace, selectedPlugin, pluginsUrl, marketplaceSkillsRetry]);

  // Fetch a skill's SKILL.md body on demand. Its keyed request stays loading
  // until the matching stream event updates the store.
  useEffect(() => {
    if (!realtime.connected || !previewSkill || !selectedPlugin || !selectedMarketplace || skillPreview) {
      return undefined;
    }
    dispatch(fetchPluginSkillContent({
      pluginsUrl,
      marketplace: selectedMarketplace,
      plugin: selectedPlugin.name,
      skill: previewSkill,
    }));
    return undefined;
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
      setPreviewSkill(null);
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
    setPreviewSkill(null);
  };

  const handleTogglePreviewSkill = (skill) => {
    setPreviewSkill((current) => (current === skill ? null : skill));
  };

  const runPluginAction = async (kind) => {
    if (!selectedPlugin || !selectedMarketplace || !realtime.connected) return;
    const plugin = selectedPlugin;
    const marketplace = selectedMarketplace;
    const key = `${marketplace}/${plugin.name}`;
    const operation = kind === 'uninstall' ? 'Uninstall plugin' : kind === 'update' ? 'Update plugin' : 'Install plugin';
    const result = await pluginAction.run(operation, async () => dispatch(
      kind === 'uninstall'
        ? uninstallPlugin({ pluginsUrl, marketplace, plugin: plugin.name })
        : installPlugin({ pluginsUrl, marketplace, plugin: plugin.name }),
    ).unwrap());
    if (!result.ok) return;
    setStatusMessage(`${operation} request acknowledged. Waiting for the live plugin update…`);
    setDismissedPluginChangeId(null);
    setAwaitingPluginChange({
      key,
      kind,
      label: plugin.displayName || plugin.name,
      requestId: result.value.request_id,
    });
  };

  const handleInstallPlugin = () => {
    const key = `${selectedMarketplace}/${selectedPlugin?.name}`;
    if (selectedPlugin && installedPlugins[key]) {
      setStatusMessage(`Plugin ${key} is already installed.`);
      return;
    }
    void runPluginAction('install');
  };

  const handleUpdatePlugin = () => { void runPluginAction('update'); };

  const handleUninstallPlugin = () => {
    const key = `${selectedMarketplace}/${selectedPlugin?.name}`;
    if (selectedPlugin && !installedPlugins[key]) {
      setStatusMessage(`Plugin ${key} is not installed.`);
      return;
    }
    void runPluginAction('uninstall');
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

  const selectedPluginKey = `${selectedMarketplace}/${selectedPlugin?.name || ''}`;
  const marketplaceSkillsResult = marketplaceSkillsByKey[selectedPluginKey];
  const previewSkills = marketplaceSkillsResult?.skills || [];
  const marketplaceSkillsFailure = marketplaceSkillsResult?.error || null;
  const marketplaceSkillsPending = realtime.connected && !isPluginInstalled && !marketplaceSkillsResult;
  const displayedSkills = isPluginInstalled ? (installedEntry?.skills || []) : previewSkills;
  const completedPluginChange = awaitingPluginChange
    && awaitingPluginChange.key === selectedPluginKey
    && pluginsState.snapshotRequestId === awaitingPluginChange.requestId
    && (awaitingPluginChange.kind === 'uninstall'
      ? !installedPlugins[awaitingPluginChange.key]
      : Boolean(installedPlugins[awaitingPluginChange.key]))
    ? awaitingPluginChange : null;
  const completedPluginMessage = completedPluginChange
    ? `Plugin ${completedPluginChange.label} ${completedPluginChange.kind === 'uninstall' ? 'uninstalled' : completedPluginChange.kind === 'update' ? 'updated' : 'installed'}.`
    : '';
  return (
    <div className="plugins-page">
      {completedPluginChange && completedPluginChange.kind !== 'uninstall' && dismissedPluginChangeId !== completedPluginChange.requestId ? (
        <div className="plugins-page__banner" role="status">
          <span>A plugin install or update just finished. Its skills will only be available starting in a new conversation.</span>
          <button type="button" onClick={() => setDismissedPluginChangeId(completedPluginChange.requestId)} aria-label="Dismiss">×</button>
        </div>
      ) : null}
      <div className="plugins-page__row">
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
        {realtime.connected && initialFailure ? (
          <InlineFailure
            failure={initialFailure}
            operation="Load plugins"
            onRetry={() => {
              dispatch(fetchMarketplaces({ marketplacesUrl }));
              dispatch(fetchPlugins({ pluginsUrl }));
            }}
          />
        ) : null}
        {realtime.connected && !initialFailure && !marketplaceOptions.length && (
          <PaneEmptyState>No marketplaces registered. Add one in Settings → Marketplaces.</PaneEmptyState>
        )}
        {realtime.connected && marketplaceOptions.length > 0 && !selectedMarketplace && (
          <PaneEmptyState>Select a marketplace to browse its plugins.</PaneEmptyState>
        )}
        {realtime.connected && selectedMarketplace && catalogRequest.loading && !catalog ? (
          <PaneEmptyState>Loading marketplace catalog…</PaneEmptyState>
        ) : null}
        {realtime.connected && selectedMarketplace && catalogRequest.error ? (
          <InlineFailure
            failure={catalogRequest.error}
            operation="Load marketplace catalog"
            onRetry={() => dispatch(fetchMarketplaceCatalog({ marketplacesUrl, name: selectedMarketplace }))}
          />
        ) : null}
        {realtime.connected && selectedMarketplace && catalog && !catalogRequest.error && visiblePlugins.length === 0 && (
          <PaneEmptyState>No plugins match the current filters.</PaneEmptyState>
        )}
        {pluginTree.length ? (
          <PaneListRows
            className="plugins-sidebar-body"
            groups={pluginTree.map((group) => ({ ...group, items: group.skills }))}
            getItemKey={(plugin) => plugin.name}
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

      <main className="plugins-editor">
        {!selectedPlugin && !initialFailure && !(selectedMarketplace && catalogRequest.error) && (
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
                  ? <span key="installed-at" className="taskid">Installed {formatCompactRelativeAge(installedEntry.installed_at)} ago</span>
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
                      disabled={!realtime.connected || pluginAction.busy}
                    />
                  ) : null}
                  {isPluginInstalled ? (
                    <DetailHeaderAction
                      icon={<UninstallActionIcon />}
                      label="Uninstall"
                      tooltip="Uninstall plugin"
                      ariaLabel="Uninstall plugin"
                      onClick={handleUninstallPlugin}
                      disabled={!realtime.connected || pluginAction.busy}
                    />
                  ) : (
                    <DetailHeaderAction
                      icon={<InstallActionIcon />}
                      label="Install"
                      tooltip="Install plugin"
                      ariaLabel="Install plugin"
                      variant="primary"
                      onClick={handleInstallPlugin}
                      disabled={!realtime.connected || pluginAction.busy}
                    />
                  )}
                </DetailHeaderActions>
              )}
            />

            <div className="plugins-detail__body plugins-detail__body--preview">
              {installedEntry?.status === 'error' && installedEntry.status_message ? (
                <PaneEmptyState>{installedEntry.status_message}</PaneEmptyState>
              ) : null}
              {pluginAction.error ? <InlineFailure failure={pluginAction.error} operation="Update plugin" /> : null}

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
                    <div><dt>Installed</dt><dd title={installedEntry.installed_at}>{formatCompactRelativeAge(installedEntry.installed_at)} ago</dd></div>
                  ) : null}
                </dl>
              </section>

              <section className="tools-section">
                <div className="tools-section__head">
                  <h2>
                    Skills provided
                    {displayedSkills.length ? <span>{displayedSkills.length}</span> : null}
                  </h2>
                </div>
                <div className="tools-tool-list">
                  {displayedSkills.length > 0 ? (
                    displayedSkills.map((skill) => (
                      <button
                        type="button"
                        className={`tools-tool-row plugins-skill-row ${previewSkill === skill ? 'is-active' : ''}`}
                        key={skill}
                        onClick={() => handleTogglePreviewSkill(skill)}
                      >
                        <span className="tools-tool-row__icon"><SkillIcon /></span>
                        <div className="tools-tool-row__body">
                          <strong>{skill}</strong>
                          <p>{isPluginInstalled ? `Installed skill from ${selectedPlugin.displayName || selectedPlugin.name}` : 'Marketplace skill'}</p>
                        </div>
                      </button>
                    ))
                  ) : marketplaceSkillsFailure && !isPluginInstalled ? (
                    <InlineFailure
                      failure={marketplaceSkillsFailure}
                      operation="Load plugin skills"
                      onRetry={() => setMarketplaceSkillsRetry((attempt) => attempt + 1)}
                    />
                  ) : (
                    <PaneEmptyState>
                      {marketplaceSkillsPending
                        ? 'Loading skills…'
                        : isPluginInstalled
                          ? 'No skills reported for this install.'
                          : 'No skills found in the marketplace source.'}
                    </PaneEmptyState>
                  )}
                </div>

                {previewSkill ? (
                  <div className="plugins-skill-preview">
                    {skillPreviewRequest.loading && !skillPreview ? (
                      <PaneEmptyState>Loading {previewSkill}…</PaneEmptyState>
                    ) : skillPreviewRequest.error && !skillPreview ? (
                      <InlineFailure
                        failure={skillPreviewRequest.error}
                        operation="Load skill preview"
                        onRetry={() => dispatch(fetchPluginSkillContent({
                          pluginsUrl,
                          marketplace: selectedMarketplace,
                          plugin: selectedPlugin.name,
                          skill: previewSkill,
                        }))}
                      />
                    ) : skillPreview ? (
                      <MarkdownContent
                        className="markdown-content plugins-skill-preview__body"
                        content={skillPreview.content}
                      />
                    ) : null}
                  </div>
                ) : null}
              </section>
            </div>

            <div className="plugins-editor-footer">
              <span>{completedPluginMessage || statusMessage || (!realtime.connected ? 'Reconnect the realtime stream to install or uninstall plugins.' : '')}</span>
            </div>
          </>
        )}
      </main>
      </div>
    </div>
  );
}
