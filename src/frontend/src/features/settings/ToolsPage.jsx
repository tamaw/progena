import { UI_EVENT } from '../../realtime/eventVocabulary';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useNavigate, useParams } from 'react-router-dom';
import { buildEntityPath, useEntityUrlSync } from '../../lib/routes';
import { dispatchQuickChatOpen, QUICK_CHAT_MODE } from '../../lib/browserContracts';

import {
  PaneEmptyState,
  PaneList,
  PaneListFilters,
  PaneListFooter,
  PaneListSearch,
  PaneListSortButton,
  PaneListToolbar,
  PaneListRows,
} from '../../components/layout/PaneList';
import PageShell from '../../components/layout/PageShell';
import CappedScrollList from '../../components/layout/CappedScrollList';
import DetailPane from '../../components/layout/DetailPane';
import DetailHeader, {
  DeleteActionIcon,
  DetailHeaderAction,
  DetailHeaderActions,
  DetailStatus,
  PauseActionIcon,
  PlayActionIcon,
} from '../../components/layout/DetailHeader';
import { MetricCard } from '../../components/DetailCards';
import InlineFailure from '../../components/InlineFailure';
import PopupModal from '../../components/PopupModal';
import useConfirmAction from '../../hooks/useConfirmAction';
import useMissingEntityRoute from '../../hooks/useMissingEntityRoute';
import useEntityDraft from '../../hooks/useEntityDraft';
import useUiAction from '../../hooks/useUiAction';
import { useWidgetsApi } from '../schedules/useWidgetsApi';
import { useConfig } from '../../context/ConfigContext';
import { useNotifications } from '../../context/NotificationContext';
import { fetchMcp, fetchMcpServerDetail, selectMcpServers, selectMcpServerDetail, selectMcpServerDetailRequest, selectMcpState } from './state/mcp';
import { formatShortLocalDateTime } from '../../lib/displayTime';
import { reportFailure } from '../../lib/failures';
import { selectRealtime } from '../../realtime/realtimeSlice';
import { entityDraftKey } from '../../store/editorDraftsSlice';
import { createUuid } from '../../lib/uuid';
import { selectAgentName } from './state/preferences';
const SERVER_FILTERS = ['All', 'Available', 'Syncing', 'Disabled', 'Errors'];
const SERVER_SORTS = ['Status', 'Name', 'Transport'];

const serverIdOf = (server) => server.id;

// MCP widget statuses from the backend (src/backend/monitoring/state.py):
// `disabled`; `stopped`/`running` for an enabled server (available whether or
// not the runtime is live); `discovering` while an enabled server has no
// discovery outcome yet; `error` when discovery failed. Anything else is
// Unknown rather than silently healthy.
const SERVER_STATUS = {
  syncing: { label: 'Syncing tools', tone: 'ok', group: 'Syncing' },
  available: { label: 'Available', tone: 'idle', group: 'Available' },
  error: { label: 'Error', tone: 'error', group: 'Needs attention' },
  unknown: { label: 'Unknown', tone: 'warning', group: 'Needs attention' },
  disabled: { label: 'Disabled', tone: 'muted', group: 'Disabled' },
};

function statusOf(server) {
  if (server.enabled === false) return 'disabled';
  const raw = String(server.status || '').toLowerCase();
  if (raw === 'error') return 'error';
  if (raw === 'discovering') return 'syncing';
  if (raw === 'running' || raw === 'stopped') return 'available';
  return 'unknown';
}

function statusLabel(status) {
  return SERVER_STATUS[status].label;
}

function statusTone(status) {
  return SERVER_STATUS[status].tone;
}

function serverMatchesFilter(server, filter) {
  const status = statusOf(server);
  if (filter === 'All') return true;
  if (filter === 'Errors') return status === 'error';
  return status === filter.toLowerCase();
}

const STATUS_RANK = { syncing: 0, available: 1, error: 2, unknown: 3, disabled: 4 };

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

const GROUP_ORDER = ['Syncing', 'Available', 'Needs attention', 'Disabled'];

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
  const agentName = useSelector(selectAgentName);
  const { updateWidget, deleteWidget } = useWidgetsApi();

  const [activeFilter, setActiveFilter] = useState('All');
  const [query, setQuery] = useState('');
  const [sortMode, setSortMode] = useState('Status');
  // Keyed by server so a failure never follows navigation to another server.
  const [configValidationState, setConfigValidationState] = useState(null);

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
    const groups = Object.fromEntries(GROUP_ORDER.map((group) => [group, []]));
    for (const server of visibleServers) {
      groups[SERVER_STATUS[statusOf(server)].group].push(server);
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
  const [configDraft, setConfigDraft, configDraftControls] = useEntityDraft(
    entityDraftKey('mcp', selectedServer?.id, 'config'),
    {
      editing: false,
      url: selectedServer?.url || '',
      description: selectedServer?.description || '',
    },
  );
  const {
    beginSave: beginConfigSave,
    clear: clearConfigDraft,
    error: configSaveError,
    failSave: failConfigSave,
    saving: configSaving,
    trackSave: trackConfigSave,
  } = configDraftControls;
  const configValidation = configValidationState && configValidationState.serverId === selectedServer?.id ? configValidationState.message : null;
  const isEditingConfig = Boolean(configDraft.editing);
  const serverAction = useUiAction(`${selectedServer?.id || 'tools'}:server`);
  const deleteAction = useConfirmAction(selectedServer?.id || 'tools');
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
    available: servers.filter((server) => statusOf(server) === 'available').length,
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
    setConfigValidationState(null);
    setConfigDraft({
      editing: true,
      url: selectedServer.url || '',
      description: selectedServer.description || '',
    });
  }, [selectedServer, setConfigDraft]);

  const cancelConfigEdit = useCallback(() => {
    if (configSaving) return;
    setConfigValidationState(null);
    clearConfigDraft();
  }, [clearConfigDraft, configSaving]);

  const saveConfigEdit = useCallback(async () => {
    if (!selectedServer || configSaving) return;
    const target = selectedServer;
    const url = configDraft.url.trim();
    const description = configDraft.description.trim();
    if (!url) {
      setConfigValidationState({ serverId: target.id, message: 'Endpoint URL is required.' });
      return;
    }
    setConfigValidationState(null);
    const ticket = beginConfigSave();
    if (!ticket) return;
    const requestId = createUuid();
    trackConfigSave(ticket, {
      requestId,
      events: [UI_EVENT.MCP_SNAPSHOT],
    });
    try {
      await updateWidget(`mcp:${target.name}`, { url, description }, { requestId });
      void refreshMcp().catch(() => {});
    } catch (error) {
      failConfigSave(ticket, error);
    }
  }, [
    beginConfigSave,
    configDraft.description,
    configDraft.url,
    configSaving,
    failConfigSave,
    refreshMcp,
    selectedServer,
    trackConfigSave,
    updateWidget,
  ]);

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
    // Creation is a Progena change request: open quick-chat primed to add an
    // MCP server (there is no REST create — the agent owns config mutation).
    dispatchQuickChatOpen({ mode: QUICK_CHAT_MODE.MCP_CREATE });
  }, []);

  // DB-backed tools surfaced by the runtime (with per-tool enabled flags),
  // discovered when the runtime initialises / refreshes its MCP context.
  const isDetailLoading = Boolean(selectedServer) && !serverDetail && serverDetailRequest.loading;
  const serverTools = Array.isArray(serverDetail?.data?.tools) ? serverDetail.data.tools : [];
  const enabledToolCount = serverTools.filter((tool) => tool.enabled !== false).length;

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
                    <span className={`sess-row__dot sess-row__dot--${statusTone(status)}`} />
                    <span className="sess-row__agent tools-pane-row__name">{server.name}</span>
                  </span>
                  <span className="sess-row__title">{server.description || server.url || '—'}</span>
                </button>
              );
            }}
          />
        )}

        <PaneListFooter>
          <span><span className="num">{totals.available}</span> available</span>
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
                        <button type="button" className="repo-btn repo-btn--ghost" onClick={cancelConfigEdit} disabled={configSaving}>Cancel</button>
                        <button type="button" className="repo-btn" onClick={saveConfigEdit} disabled={configSaving}>Save</button>
                      </>
                    ) : (
                      <button type="button" className="repo-btn repo-btn--ghost" onClick={startConfigEdit}>Edit</button>
                    )}
                  </div>
                </div>
                {isEditingConfig ? (
                  <div className="tools-config-edit">
                    <InlineFailure failure={configValidation || configSaveError} operation="Save server configuration" />
                    <label className="tools-config-field">
                      <span>Endpoint URL</span>
                      <input
                        ref={configInputRef}
                        value={configDraft.url}
                        disabled={configSaving}
                        placeholder="https://host/mcp"
                        onChange={(event) => {
                          setConfigValidationState(null);
                          setConfigDraft((current) => ({ ...current, url: event.target.value }));
                        }}
                      />
                    </label>
                    <label className="tools-config-field">
                      <span>Description</span>
                      <input
                        value={configDraft.description}
                        disabled={configSaving}
                        placeholder="What this server provides"
                        onChange={(event) => {
                          setConfigValidationState(null);
                          setConfigDraft((current) => ({ ...current, description: event.target.value }));
                        }}
                      />
                    </label>
                    <p className="tools-config-edit__hint">{configSaving ? 'Processing…' : 'Changes apply to the shared MCP registry.'}</p>
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
                <CappedScrollList className="tools-tool-list">
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
                        : `No tools discovered yet. ${agentName} lists them on its next MCP refresh.`)}</PaneEmptyState>
                  )}
                </CappedScrollList>
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
