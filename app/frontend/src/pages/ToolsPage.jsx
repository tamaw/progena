import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate, useParams } from 'react-router-dom';
import { buildEntityPath, useEntityUrlSync } from '../lib/routes';
import {
  PaneEmptyState,
  PaneList,
  PaneListFilters,
  PaneListFooter,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
  PaneListRows,
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
import { MetricCard } from '../components/common/DetailCards';
import InlineFailure from '../components/common/InlineFailure';
import PopupModal from '../components/common/PopupModal';
import useConfirmAction from '../hooks/useConfirmAction';
import useMissingEntityRoute from '../hooks/useMissingEntityRoute';
import useUiAction from '../hooks/useUiAction';
import { useWidgetsApi } from '../hooks/api/useWidgetsApi';
import { useConfig } from '../context/ConfigContext';
import { useNotifications } from '../context/NotificationContext';
import {
  fetchMcp, fetchMcpServerDetail, selectMcpServers, selectMcpServerDetail, selectMcpServerDetailRequest, selectMcpState,
} from '../store/uiDataSlice';
import { formatShortLocalDateTime } from '../lib/displayTime';
import { reportFailure } from '../lib/failures';
import { selectRealtime } from '../store/realtimeSlice';

const SERVER_FILTERS = ['All', 'Connected', 'Disabled', 'Errors'];
const SERVER_SORTS = ['Status', 'Name', 'Transport'];

const serverIdOf = (server) => server.id;

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


function ToolIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path d="M5.2 5.4 3 8l2.2 2.6M10.8 5.4 13 8l-2.2 2.6M7.2 11.5l1.6-7" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.25" />
    </svg>
  );
}

function ToolToggle({ checked, disabled, onChange }) {
  return (
    <label className={`set-switch ${checked ? 'is-on' : ''}`}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        aria-label={checked ? 'Disable tool' : 'Enable tool'}
        title={checked ? 'Disable tool' : 'Enable tool'}
        onChange={onChange}
      />
      <span className="set-switch__knob" aria-hidden="true" />
    </label>
  );
}

function ToolRow({ tool, index, serverId, onToggle }) {
  const toolEnabled = tool.enabled !== false;
  const toolAction = useUiAction(`${serverId}:tool:${tool.name || index}`);
  const { show } = useNotifications();
  const toggle = useCallback(async () => {
    if (!tool.name) return;
    const result = await toolAction.run('Update tool', () => onToggle(tool));
    if (!result.ok && result.error) reportFailure(show, 'Update tool', result.error);
  }, [onToggle, show, tool, toolAction]);

  return (
    <article className={`tools-tool-row ${toolEnabled ? '' : 'tools-tool-row--disabled'}`}>
      <span className="tools-tool-row__icon"><ToolIcon /></span>
      <div className="tools-tool-row__body">
        <strong>{tool.original_name || tool.name || 'tool'}</strong>
        <p>{tool.description || 'No description provided.'}</p>
      </div>
      <ToolToggle checked={toolEnabled} disabled={toolAction.busy} onChange={toggle} />
    </article>
  );
}

export default function ToolsPage() {
  const dispatch = useDispatch();
  const navigate = useNavigate();
  // `titleSlug` is display context only (e.g. /tools/mcp%3Ajina/jina) --
  // selection is driven entirely by the id, never by the slug.
  const { serverId: urlServerId } = useParams();
  const selectedServerId = typeof urlServerId === 'string' && urlServerId.trim().length > 0
    ? urlServerId
    : null;
  const { getApiUrl } = useConfig();
  const { show } = useNotifications();
  const realtime = useSelector(selectRealtime);
  const mcpServers = useSelector(selectMcpServers);
  const mcpState = useSelector(selectMcpState);
  const { updateWidget, deleteWidget } = useWidgetsApi();

  const [activeFilter, setActiveFilter] = useState('All');
  const [query, setQuery] = useState('');
  const [sortMode, setSortMode] = useState('Status');
  const [configValidation, setConfigValidation] = useState(null);

  const [isEditingConfig, setIsEditingConfig] = useState(false);
  const [configUrl, setConfigUrl] = useState('');
  const [configDesc, setConfigDesc] = useState('');
  const configInputRef = useRef(null);


  const mcpUrl = getApiUrl('mcp');
  // The catalogue is hydrated only by `mcp.snapshot` on the canonical stream.
  const refreshMcp = useCallback(() => dispatch(fetchMcp({ mcpUrl })).unwrap(), [dispatch, mcpUrl]);
  const retryMcp = useCallback(() => { void refreshMcp().catch(() => {}); }, [refreshMcp]);

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

  const selectedServer = useMemo(
    () => servers.find((server) => server.id === selectedServerId)
      || (!selectedServerId ? visibleServers[0] || servers[0] || null : null),
    [selectedServerId, servers, visibleServers],
  );
  const selectedStatus = selectedServer ? statusOf(selectedServer) : 'disabled';
  // Per-tool schemas and log history are detail-only fields, fetched on
  // demand per server (the list snapshot carries scalar fields only).
  const serverDetail = useSelector((state) => selectMcpServerDetail(state, selectedServer?.id));
  const serverDetailRequest = useSelector((state) => selectMcpServerDetailRequest(state, selectedServer?.id));
  const configAction = useUiAction(`${selectedServer?.id || 'tools'}:config`);
  const serverAction = useUiAction(`${selectedServer?.id || 'tools'}:server`);
  const deleteAction = useConfirmAction(selectedServer?.id || 'tools');
  const [configServerId, setConfigServerId] = useState(selectedServer?.id);
  if (configServerId !== selectedServer?.id) {
    setConfigServerId(selectedServer?.id);
    setIsEditingConfig(false);
    setConfigValidation(null);
  }
  const runQuickAction = useCallback(async (action, operation, task) => {
    const result = await action.run(operation, task);
    if (!result.ok && result.error) reportFailure(show, operation, result.error);
  }, [show]);

  // Keep the URL in sync with whichever server is actually selected. A routed
  // server id stays authoritative; if it disappears after hydration, the effect
  // below clears back to /tools instead of rewriting stale detail state.
  // The title slug is cosmetic; only the id segment is ever read back.
  useEntityUrlSync('/tools', selectedServer?.id, selectedServer?.name);

  const totals = useMemo(() => ({
    connected: servers.filter((server) => statusOf(server) === 'connected').length,
    disabled: servers.filter((server) => statusOf(server) === 'disabled').length,
    errors: servers.filter((server) => statusOf(server) === 'error').length,
  }), [servers]);

  useEffect(() => {
    if (!realtime.connected) return;
    void refreshMcp().catch(() => {});
  }, [realtime.connected, refreshMcp]);

  const loadServerDetail = useCallback(() => {
    const id = selectedServer?.id;
    if (!id || !realtime.connected || serverDetailRequest.loading) return;
    void dispatch(fetchMcpServerDetail({ mcpUrl, id })).unwrap().catch(() => {});
  }, [dispatch, mcpUrl, realtime.connected, selectedServer?.id, serverDetailRequest.loading]);

  useEffect(() => {
    if (!serverDetail && !serverDetailRequest.loading && !serverDetailRequest.error) {
      loadServerDetail();
    }
  }, [loadServerDetail, serverDetail, serverDetailRequest.error, serverDetailRequest.loading]);

  useMissingEntityRoute({
    hydrated: mcpState.loaded,
    entityId: selectedServerId,
    entities: servers,
    getEntityId: serverIdOf,
    fallbackPath: '/tools',
  });

  // Draft state resets during the selection identity transition above, before
  // a different server can render with the previous server's configuration.

  useEffect(() => {
    if (!isEditingConfig) return undefined;
    configInputRef.current?.focus();
    configInputRef.current?.select();
    return undefined;
  }, [isEditingConfig]);

  const toggleEnabled = useCallback(() => {
    if (!selectedServer) return;
    void runQuickAction(serverAction, 'Update server', async () => {
      await updateWidget(`mcp:${selectedServer.name}`, { enabled: selectedServer.enabled === false });
      await refreshMcp();
    });
  }, [refreshMcp, runQuickAction, selectedServer, serverAction, updateWidget]);

  const updateTool = useCallback(async (tool) => {
    await updateWidget(`mcp_tool:${tool.name}`, { enabled: tool.enabled === false });
    await refreshMcp();
  }, [refreshMcp, updateWidget]);

  const startConfigEdit = useCallback(() => {
    if (!selectedServer) return;
    setConfigValidation(null);
    setConfigUrl(selectedServer.url || '');
    setConfigDesc(selectedServer.description || '');
    setIsEditingConfig(true);
  }, [selectedServer]);

  const cancelConfigEdit = useCallback(() => {
    setConfigValidation(null);
    setIsEditingConfig(false);
  }, []);

  const saveConfigEdit = useCallback(() => {
    if (!selectedServer) return;
    const url = configUrl.trim();
    const description = configDesc.trim();
    if (!url) {
      setConfigValidation('Endpoint URL is required.');
      return;
    }
    setConfigValidation(null);
    void configAction.run('Save server configuration', async ({ isCurrent }) => {
      await updateWidget(`mcp:${selectedServer.name}`, { url, description });
      await refreshMcp();
      if (isCurrent()) setIsEditingConfig(false);
    });
  }, [configAction, configDesc, configUrl, refreshMcp, selectedServer, updateWidget]);

  const handleDelete = useCallback(() => {
    if (!selectedServer) return;
    const server = selectedServer;
    deleteAction.request('Delete server', async ({ isCurrent }) => {
      await deleteWidget(`mcp:${server.name}`);
      await refreshMcp();
      if (isCurrent()) navigate('/tools', { replace: true });
    });
  }, [deleteAction, deleteWidget, navigate, refreshMcp, selectedServer]);

  const addServer = useCallback(() => {
    // Creation is a Progenitor change request: open quick-chat primed to add an
    // MCP server (there is no REST create — the agent owns config mutation).
    window.dispatchEvent(new CustomEvent('progenitor:open-quick-chat', {
      detail: { mode: 'mcp-create' },
    }));
  }, []);

  // DB-backed tools surfaced by the runtime (with per-tool enabled flags),
  // discovered when the runtime initialises / refreshes its MCP context.
  const isDetailLoading = Boolean(selectedServer) && !serverDetail && serverDetailRequest.loading;
  const serverTools = Array.isArray(serverDetail?.data?.tools) ? serverDetail.data.tools : [];
  const enabledToolCount = serverTools.filter((tool) => tool.enabled !== false).length;
  const selectedLogs = Array.isArray(serverDetail?.data?.logs) ? serverDetail.data.logs.slice(0, 5) : [];

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

        {mcpState.error ? <InlineFailure failure={mcpState.error} operation="Load MCP servers" onRetry={retryMcp} /> : null}
        {servers.length === 0 ? (
          mcpState.error ? null : <PaneEmptyState>No MCP servers configured.</PaneEmptyState>
        ) : visibleServers.length === 0 ? (
          <PaneEmptyState>No MCP servers match this view.</PaneEmptyState>
        ) : (
          <PaneListRows
            groups={GROUP_ORDER
              .filter((group) => groupedServers[group]?.length)
              .map((group) => ({
                key: group,
                title: group,
                items: groupedServers[group],
              }))}
            getItemKey={(server) => server.id}
            renderItem={(server) => {
              const status = statusOf(server);
              return (
                <button
                  type="button"
                  className={`sess-row tools-pane-row tools-pane-row--${status} ${selectedServer?.id === server.id ? 'is-selected' : ''}`}
                  aria-label={`${server.name}, ${statusLabel(status)}, ${server.transport || 'mcp'}`}
                  onClick={() => navigate(buildEntityPath('/tools', server.id, server.name), { replace: true })}
                >
                  <span className="sess-row__top">
                    <span className={`sess-row__dot tools-status-dot--${status}`} />
                    <span className="sess-row__agent tools-pane-row__name">{server.name}</span>
                  </span>
                  <span className="sess-row__title">{server.description || server.url || '—'}</span>
                </button>
              );
            }}
          />
        )}

        <PaneListFooter>
          <span><span className="num">{totals.connected}</span> connected</span>
          <span><span className="num">{totals.disabled}</span> disabled</span>
          <span><span className="num">{totals.errors}</span> errors</span>
        </PaneListFooter>
      </PaneList>

      <DetailPane className="detail-pane--dense tools-detail" label="MCP server detail">
        {!selectedServer ? (
          <div className="detail-pane__body">
            <PaneEmptyState>Select a server to view its details.</PaneEmptyState>
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
                    disabled={serverAction.busy || deleteAction.busy}
                    onClick={toggleEnabled}
                  />
                  <DetailHeaderAction
                    icon={<DeleteActionIcon />}
                    label="Remove"
                    tooltip="Remove server"
                    ariaLabel="Remove server"
                    variant="danger"
                    disabled={serverAction.busy || deleteAction.busy}
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
              {serverDetailRequest.error ? (
                <InlineFailure
                  failure={serverDetailRequest.error}
                  operation="Load server details"
                  onRetry={loadServerDetail}
                />
              ) : null}

              <section className="tools-metrics" aria-label="MCP server metrics">
                <MetricCard label="Status" value={statusLabel(selectedStatus)} detail={selectedServer.enabled === false ? 'disabled' : 'enabled'} />
                <MetricCard label="Transport" value={selectedServer.transport || 'mcp'} detail="protocol" />
                <MetricCard
                  label="Tools"
                  value={isDetailLoading ? '…' : (serverTools.length ? `${enabledToolCount}/${serverTools.length}` : '—')}
                  detail={isDetailLoading ? 'loading' : (serverTools.length ? 'enabled' : 'none discovered')}
                />
                <MetricCard
                  label="Updated"
                  value={selectedServer.updated_at ? formatShortLocalDateTime(selectedServer.updated_at) : '—'}
                  detail="last change"
                />
              </section>

              <section className="tools-section">
                <div className="tools-section__head">
                  <h2>Configuration</h2>
                  <div>
                    {isEditingConfig ? (
                      <>
                        <button type="button" className="repo-btn repo-btn--ghost" onClick={cancelConfigEdit} disabled={configAction.busy}>Cancel</button>
                        <button type="button" className="repo-btn" onClick={saveConfigEdit} disabled={configAction.busy}>Save</button>
                      </>
                    ) : (
                      <button type="button" className="repo-btn repo-btn--ghost" onClick={startConfigEdit}>Edit</button>
                    )}
                  </div>
                </div>
                {isEditingConfig ? (
                  <div className="tools-config-edit">
                    <InlineFailure failure={configValidation || configAction.error} operation="Save server configuration" />
                    <label className="tools-config-field">
                      <span>Endpoint URL</span>
                      <input
                        ref={configInputRef}
                        value={configUrl}
                        disabled={configAction.busy}
                        placeholder="https://host/mcp"
                        onChange={(event) => {
                          setConfigValidation(null);
                          configAction.clearError();
                          setConfigUrl(event.target.value);
                        }}
                      />
                    </label>
                    <label className="tools-config-field">
                      <span>Description</span>
                      <input
                        value={configDesc}
                        disabled={configAction.busy}
                        placeholder="What this server provides"
                        onChange={(event) => {
                          setConfigValidation(null);
                          configAction.clearError();
                          setConfigDesc(event.target.value);
                        }}
                      />
                    </label>
                    <p className="tools-config-edit__hint">{configAction.busy ? 'Processing…' : 'Changes apply to the shared MCP registry.'}</p>
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
                  {serverTools.length ? serverTools.map((tool, index) => (
                    <ToolRow
                      key={tool.name || index}
                      tool={tool}
                      index={index}
                      serverId={selectedServer.id}
                      onToggle={updateTool}
                    />
                  )) : (
                    <PaneEmptyState>{isDetailLoading
                      ? 'Loading tools…'
                      : (selectedServer.enabled === false
                        ? 'Enable the server to discover its tools.'
                        : 'No tools discovered yet. Progenitor lists them on its next MCP refresh.')}</PaneEmptyState>
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
                        <p>{formatShortLocalDateTime(entry.timestamp || entry.time || entry.updated_at) || 'recent'}</p>
                      </div>
                      <span className="tools-tool-row__calls">{String(entry.level || entry.source || 'info').toLowerCase()}</span>
                    </article>
                  )) : (
                    <PaneEmptyState>{isDetailLoading ? 'Loading activity…' : 'No recent activity for this server.'}</PaneEmptyState>
                  )}
                </div>
              </section>
            </div>
          </>
        )}
      </DetailPane>
      {deleteAction.open && selectedServer ? (
        <PopupModal
          eyebrow="Remove MCP server"
          title={`Remove “${selectedServer.name}”?`}
          message="This removes the server from the MCP registry. This can’t be undone."
          error={deleteAction.error}
          confirmLabel="Remove server"
          busyLabel="Removing…"
          busy={deleteAction.busy}
          onClose={deleteAction.close}
          onConfirm={deleteAction.confirm}
        />
      ) : null}
    </PageShell>
  );
}
