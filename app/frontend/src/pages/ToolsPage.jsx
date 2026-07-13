import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useParams } from 'react-router-dom';
import { useEntityUrlSync } from '../lib/routes';
import {
  PaneList,
  PaneListFilters,
  PaneListFooter,
  PaneListGroup,
  PaneListScroll,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
} from '../components/layout/PaneList';
import PageShell from '../components/layout/PageShell';
import DetailPane from '../components/layout/DetailPane';
import DetailHeader, {
  DeleteActionIcon,
  DetailHeaderAction,
  DetailHeaderActions,
  DetailStatus,
  PauseActionIcon,
  PlayActionIcon,
} from '../components/layout/DetailHeader';
import { useWidgetsApi } from '../hooks/api/useWidgetsApi';
import { useConfig } from '../context/ConfigContext';
import { fetchMcp, selectMcpServers } from '../store/uiDataSlice';
import { selectRealtime } from '../store/realtimeSlice';

const SERVER_FILTERS = ['All', 'Connected', 'Disabled', 'Errors'];
const SERVER_SORTS = ['Status', 'Name', 'Transport'];

// Derive a coarse status from the live MCP widget. Disabled wins (the runtime
// won't connect a disabled server); otherwise an error-ish status surfaces, and
// everything else counts as connected.
function statusOf(server) {
  if (server.enabled === false) return 'disabled';
  const raw = String(server.status || '').toLowerCase();
  if (raw.includes('error') || raw.includes('fail') || raw.includes('unhealthy')) return 'error';
  // An enabled server counts as connected/available even if the runtime reports
  // it as "stopped" (not actively connected in this read path).
  return 'connected';
}

function statusLabel(status) {
  if (status === 'connected') return 'Connected';
  if (status === 'disabled') return 'Disabled';
  return 'Error';
}

function statusTone(status) {
  if (status === 'connected') return 'ok';
  if (status === 'error') return 'error';
  return 'muted';
}

function serverMatchesFilter(server, filter) {
  const status = statusOf(server);
  if (filter === 'All') return true;
  if (filter === 'Errors') return status === 'error';
  return status === filter.toLowerCase();
}

const STATUS_RANK = { connected: 0, error: 1, disabled: 2 };

function compareServers(a, b, sortMode) {
  if (sortMode === 'Name') {
    return String(a.name || '').localeCompare(String(b.name || ''));
  }
  if (sortMode === 'Transport') {
    const delta = String(a.transport || '').localeCompare(String(b.transport || ''));
    if (delta !== 0) return delta;
  } else if (sortMode === 'Status') {
    const delta = (STATUS_RANK[statusOf(a)] ?? 9) - (STATUS_RANK[statusOf(b)] ?? 9);
    if (delta !== 0) return delta;
  }
  return String(a.name || '').localeCompare(String(b.name || ''));
}

const STATUS_GROUP = { connected: 'Connected', disabled: 'Disabled', error: 'Needs attention' };
const GROUP_ORDER = ['Connected', 'Needs attention', 'Disabled'];

function parseDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatLogTime(value) {
  const date = parseDate(value);
  if (!date) return '';
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function ToolIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="M5.2 5.4 3 8l2.2 2.6M10.8 5.4 13 8l-2.2 2.6M7.2 11.5l1.6-7" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.25" />
    </svg>
  );
}

function ToolToggle({ checked, disabled, onChange }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={checked ? 'Disable tool' : 'Enable tool'}
      title={checked ? 'Disable tool' : 'Enable tool'}
      className={`tools-toggle ${checked ? 'is-on' : 'is-off'}`}
      disabled={disabled}
      onClick={onChange}
    >
      <span />
    </button>
  );
}

function MetricCard({ label, value, detail }) {
  return (
    <article className="tools-metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

export default function ToolsPage() {
  const dispatch = useDispatch();
  // `titleSlug` is display context only (e.g. /tools/mcp%3Ajina/jina) --
  // selection is driven entirely by the id, never by the slug.
  const { serverId: urlServerId } = useParams();
  const { getApiUrl } = useConfig();
  const realtime = useSelector(selectRealtime);
  const mcpServers = useSelector(selectMcpServers);
  const { updateWidget, deleteWidget } = useWidgetsApi();

  const [activeFilter, setActiveFilter] = useState('All');
  const [query, setQuery] = useState('');
  const [sortMode, setSortMode] = useState('Status');
  const [selectedId, setSelectedId] = useState(() => urlServerId || null);
  const [mutating, setMutating] = useState(false);
  const [error, setError] = useState(null);

  const [isEditingConfig, setIsEditingConfig] = useState(false);
  const [configUrl, setConfigUrl] = useState('');
  const [configDesc, setConfigDesc] = useState('');
  const configInputRef = useRef(null);

  const [togglingTool, setTogglingTool] = useState(null);

  const mcpUrl = getApiUrl('mcp');
  // Request the MCP list; the snapshot comes back on the realtime stream as
  // `mcp.snapshot`, and live MCP changes arrive the same way (Postgres
  // NOTIFY -> SSE). The GET just acknowledges and triggers the broadcast.
  const refreshMcp = useCallback(
    () => dispatch(fetchMcp({ mcpUrl }))
      .unwrap()
      .catch((err) => setError(err.message || 'Failed to load servers.')),
    [dispatch, mcpUrl],
  );

  // The server's rich fields (enabled/transport/url/kind) live under `data`;
  // flatten so the page can read them directly. The store slice already holds
  // only MCP widgets, so no type filter is needed here.
  const servers = useMemo(
    () => (Array.isArray(mcpServers)
      ? mcpServers.map((widget) => ({ ...widget, ...(widget.data || {}) }))
      : []),
    [mcpServers],
  );

  const visibleServers = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    const filtered = servers.filter((server) => {
      if (!serverMatchesFilter(server, activeFilter)) return false;
      if (!normalizedQuery) return true;
      const haystack = `${server.name} ${server.transport} ${server.url} ${server.description}`.toLowerCase();
      return haystack.includes(normalizedQuery);
    });
    return [...filtered].sort((a, b) => compareServers(a, b, sortMode));
  }, [servers, activeFilter, query, sortMode]);

  const groupedServers = useMemo(() => {
    const groups = { Connected: [], 'Needs attention': [], Disabled: [] };
    for (const server of visibleServers) {
      groups[STATUS_GROUP[statusOf(server)]].push(server);
    }
    return groups;
  }, [visibleServers]);

  const selectedServer =
    servers.find((server) => server.id === selectedId) || visibleServers[0] || servers[0] || null;
  const selectedStatus = selectedServer ? statusOf(selectedServer) : 'disabled';

  // Keep the URL in sync with whichever server is actually selected -- a
  // deep-linked or stale id falls back to the top visible server the same
  // way `selectedServer` already does above. The title slug is cosmetic;
  // only the id segment is ever read back.
  useEntityUrlSync('/tools', selectedServer?.id, selectedServer?.name);

  const totals = useMemo(() => ({
    connected: servers.filter((server) => statusOf(server) === 'connected').length,
    disabled: servers.filter((server) => statusOf(server) === 'disabled').length,
    errors: servers.filter((server) => statusOf(server) === 'error').length,
  }), [servers]);

  // Request the MCP list once the realtime stream is connected, so the
  // `mcp.snapshot` it triggers isn't missed before the EventSource is listening.
  useEffect(() => {
    if (!realtime.connected) return;
    refreshMcp();
  }, [realtime.connected, refreshMcp]);

  // Leave edit mode whenever the selected server changes so a stale draft never
  // leaks across selections.
  useEffect(() => {
    setIsEditingConfig(false);
    setError(null);
  }, [selectedServer?.id]);

  useEffect(() => {
    if (!isEditingConfig) return undefined;
    configInputRef.current?.focus();
    configInputRef.current?.select();
    return undefined;
  }, [isEditingConfig]);

  const toggleEnabled = useCallback(async () => {
    if (!selectedServer) return;
    setMutating(true);
    setError(null);
    try {
      await updateWidget(`mcp:${selectedServer.name}`, { enabled: selectedServer.enabled === false });
      await refreshMcp();
    } catch (err) {
      setError(err.message || 'Failed to update server.');
    } finally {
      setMutating(false);
    }
  }, [refreshMcp, selectedServer, updateWidget]);

  const toggleTool = useCallback(async (tool) => {
    if (!tool?.name) return;
    setTogglingTool(tool.name);
    setError(null);
    try {
      await updateWidget(`mcp_tool:${tool.name}`, { enabled: tool.enabled === false });
      await refreshMcp();
    } catch (err) {
      setError(err.message || 'Failed to update tool.');
    } finally {
      setTogglingTool(null);
    }
  }, [refreshMcp, updateWidget]);

  const startConfigEdit = useCallback(() => {
    if (!selectedServer) return;
    setConfigUrl(selectedServer.url || '');
    setConfigDesc(selectedServer.description || '');
    setIsEditingConfig(true);
  }, [selectedServer]);

  const cancelConfigEdit = useCallback(() => {
    setIsEditingConfig(false);
  }, []);

  const saveConfigEdit = useCallback(async () => {
    if (!selectedServer) return;
    const url = configUrl.trim();
    const description = configDesc.trim();
    if (!url) {
      setError('Endpoint URL is required.');
      return;
    }
    setMutating(true);
    setError(null);
    try {
      await updateWidget(`mcp:${selectedServer.name}`, { url, description });
      await refreshMcp();
      setIsEditingConfig(false);
    } catch (err) {
      setError(err.message || 'Failed to save configuration.');
    } finally {
      setMutating(false);
    }
  }, [configDesc, configUrl, refreshMcp, selectedServer, updateWidget]);

  const handleDelete = useCallback(async () => {
    if (!selectedServer) return;
    const confirmed = window.confirm(
      `Remove MCP server "${selectedServer.name}"? This can't be undone.`,
    );
    if (!confirmed) return;
    setMutating(true);
    setError(null);
    try {
      await deleteWidget(`mcp:${selectedServer.name}`);
      setSelectedId(null);
      await refreshMcp();
    } catch (err) {
      setError(err.message || 'Failed to remove server.');
    } finally {
      setMutating(false);
    }
  }, [deleteWidget, refreshMcp, selectedServer]);

  const addServer = useCallback(() => {
    // Creation is a Progenitor change request: open quick-chat primed to add an
    // MCP server (there is no REST create — the agent owns config mutation).
    window.dispatchEvent(new CustomEvent('progenitor:open-quick-chat', {
      detail: { mode: 'mcp-create' },
    }));
  }, []);

  // DB-backed tools surfaced by the runtime (with per-tool enabled flags),
  // discovered when the runtime initialises / refreshes its MCP context.
  const serverTools = Array.isArray(selectedServer?.tools) ? selectedServer.tools : [];
  const enabledToolCount = serverTools.filter((tool) => tool.enabled !== false).length;
  const selectedLogs = Array.isArray(selectedServer?.logs) ? selectedServer.logs.slice(0, 5) : [];

  return (
    <PageShell className="tools-page">
      <PaneList className="tools-pane" label="MCP servers">
        <PaneListFilters
          filters={SERVER_FILTERS}
          activeFilter={activeFilter}
          onFilterChange={setActiveFilter}
          actionLabel="+ New"
          onAction={addServer}
        />
        <PaneListToolbar>
          <PaneListSearch value={query} placeholder="Search servers..." onChange={setQuery} />
          <PaneListSortButton title="Sort servers" options={SERVER_SORTS} value={sortMode} onSelect={setSortMode} />
        </PaneListToolbar>

        <PaneListScroll>
          {servers.length === 0 ? (
            <div className="tools-empty-state">
              No MCP servers configured. Use “+ New” to ask Progenitor to connect one.
            </div>
          ) : visibleServers.length === 0 ? (
            <div className="tools-empty-state">No MCP servers match this view.</div>
          ) : (
            GROUP_ORDER.map((group) => (
              groupedServers[group]?.length ? (
                <PaneListGroup key={group} title={group} count={groupedServers[group].length}>
                  {groupedServers[group].map((server) => {
                    const status = statusOf(server);
                    return (
                      <button
                        type="button"
                        key={server.id}
                        className={`sess-row tools-pane-row tools-pane-row--${status} ${selectedServer?.id === server.id ? 'is-selected' : ''}`}
                        aria-label={`${server.name}, ${statusLabel(status)}, ${server.transport || 'mcp'}`}
                        onClick={() => setSelectedId(server.id)}
                      >
                        <span className="sess-row__top">
                          <span className={`sess-row__dot tools-status-dot--${status}`} />
                          <span className="sess-row__agent tools-pane-row__name">{server.name}</span>
                        </span>
                        <span className="sess-row__title">{server.description || server.url || '—'}</span>
                      </button>
                    );
                  })}
                </PaneListGroup>
              ) : null
            ))
          )}
        </PaneListScroll>

        <PaneListFooter>
          <span><span className="num">{totals.connected}</span> connected</span>
          <span><span className="num">{totals.disabled}</span> disabled</span>
          <span><span className="num">{totals.errors}</span> errors</span>
        </PaneListFooter>
      </PaneList>

      <DetailPane className="detail-pane--dense tools-detail" label="MCP server detail">
        {!selectedServer ? (
          <div className="detail-pane__body">
            <div className="tools-empty-state">
              Select a server, or use “+ New” to ask Progenitor to connect one.
            </div>
          </div>
        ) : (
          <>
            <DetailHeader
              title={selectedServer.name}
              actions={(
                <DetailHeaderActions menuLabel="Server actions">
                  <DetailHeaderAction
                    icon={selectedServer.enabled === false ? <PlayActionIcon /> : <PauseActionIcon />}
                    label={selectedServer.enabled === false ? 'Enable' : 'Disable'}
                    tooltip={selectedServer.enabled === false ? 'Enable server' : 'Disable server'}
                    ariaLabel={selectedServer.enabled === false ? 'Enable server' : 'Disable server'}
                    variant="primary"
                    disabled={mutating}
                    onClick={toggleEnabled}
                  />
                  <DetailHeaderAction
                    icon={<DeleteActionIcon />}
                    label="Remove"
                    tooltip="Remove server"
                    ariaLabel="Remove server"
                    variant="danger"
                    disabled={mutating}
                    onClick={handleDelete}
                  />
                </DetailHeaderActions>
              )}
              meta={[
                <DetailStatus key="status" tone={statusTone(selectedStatus)}>{statusLabel(selectedStatus)}</DetailStatus>,
                <span key="transport" className="muted">{selectedServer.transport || 'mcp'}</span>,
                selectedServer.url ? <span key="url" className="muted tools-detail__url">{selectedServer.url}</span> : null,
                selectedServer.kind ? <span key="kind" className="muted">{selectedServer.kind}</span> : null,
              ]}
            />

            <div className="detail-pane__body">
              {error ? <div className="tools-empty-state">{error}</div> : null}

              <section className="tools-metrics" aria-label="MCP server metrics">
                <MetricCard label="Status" value={statusLabel(selectedStatus)} detail={selectedServer.enabled === false ? 'disabled' : 'enabled'} />
                <MetricCard label="Transport" value={selectedServer.transport || 'mcp'} detail="protocol" />
                <MetricCard
                  label="Tools"
                  value={serverTools.length ? `${enabledToolCount}/${serverTools.length}` : '—'}
                  detail={serverTools.length ? 'enabled' : 'none discovered'}
                />
                <MetricCard
                  label="Updated"
                  value={selectedServer.updated_at ? formatLogTime(selectedServer.updated_at) : '—'}
                  detail="last change"
                />
              </section>

              <section className="tools-section">
                <div className="tools-section__head">
                  <h2>Configuration</h2>
                  <div>
                    {isEditingConfig ? (
                      <>
                        <button type="button" className="repo-btn repo-btn--ghost" onClick={cancelConfigEdit} disabled={mutating}>Cancel</button>
                        <button type="button" className="repo-btn" onClick={saveConfigEdit} disabled={mutating}>Save</button>
                      </>
                    ) : (
                      <button type="button" className="repo-btn repo-btn--ghost" onClick={startConfigEdit}>Edit</button>
                    )}
                  </div>
                </div>
                {isEditingConfig ? (
                  <div className="tools-config-edit">
                    <label className="tools-config-field">
                      <span>Endpoint URL</span>
                      <input
                        ref={configInputRef}
                        value={configUrl}
                        disabled={mutating}
                        placeholder="https://host/mcp"
                        onChange={(event) => setConfigUrl(event.target.value)}
                      />
                    </label>
                    <label className="tools-config-field">
                      <span>Description</span>
                      <input
                        value={configDesc}
                        disabled={mutating}
                        placeholder="What this server provides"
                        onChange={(event) => setConfigDesc(event.target.value)}
                      />
                    </label>
                    <p className="tools-config-edit__hint">{mutating ? 'Processing…' : 'Changes apply to the shared MCP registry.'}</p>
                  </div>
                ) : (
                  <dl className="tools-connection-grid">
                    <div><dt>Endpoint</dt><dd>{selectedServer.url || '—'}</dd></div>
                    <div><dt>Transport</dt><dd>{selectedServer.transport || 'mcp'}</dd></div>
                    <div><dt>Enabled</dt><dd>{selectedServer.enabled === false ? 'no' : 'yes'}</dd></div>
                    <div><dt>Description</dt><dd>{selectedServer.description || '—'}</dd></div>
                  </dl>
                )}
              </section>

              <section className="tools-section">
                <div className="tools-section__head">
                  <h2>
                    Tools {serverTools.length ? <span>{enabledToolCount}/{serverTools.length} enabled</span> : null}
                  </h2>
                </div>
                <div className="tools-tool-list">
                  {serverTools.length ? serverTools.map((tool, index) => {
                    const toolEnabled = tool.enabled !== false;
                    return (
                      <article
                        className={`tools-tool-row ${toolEnabled ? '' : 'tools-tool-row--disabled'}`}
                        key={tool.name || index}
                      >
                        <span className="tools-tool-row__icon"><ToolIcon /></span>
                        <div className="tools-tool-row__body">
                          <strong>{tool.original_name || tool.name || 'tool'}</strong>
                          <p>{tool.description || 'No description provided.'}</p>
                        </div>
                        <ToolToggle
                          checked={toolEnabled}
                          disabled={togglingTool === tool.name}
                          onChange={() => toggleTool(tool)}
                        />
                      </article>
                    );
                  }) : (
                    <div className="tools-empty-state">
                      {selectedServer.enabled === false
                        ? 'Enable the server to discover its tools.'
                        : 'No tools discovered yet. Progenitor lists them on its next MCP refresh.'}
                    </div>
                  )}
                </div>
              </section>

              <section className="tools-section">
                <h2>Activity</h2>
                <div className="tools-tool-list">
                  {selectedLogs.length ? selectedLogs.map((entry, index) => (
                    <article className="tools-tool-row" key={entry.id || entry.timestamp || index}>
                      <span className="tools-tool-row__icon">log</span>
                      <div className="tools-tool-row__body">
                        <strong>{entry.message || entry.event || 'Server activity'}</strong>
                        <p>{formatLogTime(entry.timestamp || entry.time || entry.updated_at) || 'recent'}</p>
                      </div>
                      <span className="tools-tool-row__calls">{String(entry.level || entry.source || 'info').toLowerCase()}</span>
                    </article>
                  )) : (
                    <div className="tools-empty-state">No recent activity for this server.</div>
                  )}
                </div>
              </section>
            </div>
          </>
        )}
      </DetailPane>
    </PageShell>
  );
}
