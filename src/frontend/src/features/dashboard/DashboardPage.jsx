import { useMemo, useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { useDashboardData } from './useDashboardData';
import InlineFailure from '../../components/InlineFailure';

const EMPTY_LIST = [];

function formatUptime(seconds) {
  if (!seconds) return '--';
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

function formatRelativeTime(value) {
  if (!value) return '--';
  const ts = Date.parse(value);
  if (Number.isNaN(ts)) return value;
  const diffSeconds = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (diffSeconds < 60) return 'now';
  if (diffSeconds < 3600) return `${Math.floor(diffSeconds / 60)}m`;
  if (diffSeconds < 86400) return `${Math.floor(diffSeconds / 3600)}h`;
  return `${Math.floor(diffSeconds / 86400)}d`;
}

function normalizeStatus(value) {
  if (value === 'stopped') return 'offline';
  if (['busy', 'executing'].includes(value)) return 'running';
  if (value === 'failed') return 'error';
  return value || 'idle';
}

function isHealthyResourceStatus(status) {
  const s = normalizeStatus(status);
  return ['ready', 'connected', 'active', 'running', 'idle'].includes(s);
}

function isUnhealthyResourceStatus(status) {
  const s = normalizeStatus(status);
  return ['offline', 'error'].includes(s);
}

function widgetStatus(widget) {
  return widget.status || widget.data?.agentStatus || widget.data?.status || 'idle';
}

function widgetLabel(widget) {
  return widget.title || widget.data?.title || widget.name || widget.id;
}

function widgetDescription(widget) {
  return widget.summary || widget.data?.summaryState || widget.data?.description || 'No description';
}

function StatusPill({ status }) {
  const normalized = normalizeStatus(status);
  return <span className={`dashboard-status-pill dashboard-status-pill--${normalized}`}>{normalized}</span>;
}

function RunStatusPill({ status }) {
  return <span className={`dashboard-status-pill dashboard-status-pill--${status || 'queued'}`}>{status || 'queued'}</span>;
}

function MetricCard({ label, value, detail, icon }) {
  return (
    <article className="dashboard-metric-card">
      <div className="dashboard-metric-card__heading">
        <span className="dashboard-metric-card__label">{label}</span>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d={icon} />
        </svg>
      </div>
      <strong className="dashboard-metric-card__value">{value}</strong>
      <span className="dashboard-metric-card__detail">{detail}</span>
    </article>
  );
}

function MiniRow({ children, stacked }) {
  return <div className={`dashboard-mini-row${stacked ? ' dashboard-mini-row--stacked' : ''}`}>{children}</div>;
}

function EmptyState({ children, small }) {
  return <div className={`dashboard-empty-state${small ? ' small' : ''}`}>{children}</div>;
}

function WidgetDetail({ widget, onClose }) {
  const status = widgetStatus(widget);
  const logs = (widget.logs || widget.data?.logs || []).slice(-12);
  const linkTarget = widget.type === 'mcp' ? `/tools/${encodeURIComponent(widget.id)}` : '/agents';
  return (
    <div id="dashboard-resource-details" className="dashboard-widget-detail" role="region" aria-label={`${widgetLabel(widget)} details`}>
      <div className="dashboard-widget-detail__section">
        <h4>Status</h4>
        <StatusPill status={status} />
      </div>
      <div className="dashboard-widget-detail__section">
        <h4>Summary</h4>
        <p>{widgetDescription(widget)}</p>
      </div>
      <div className="dashboard-widget-detail__section">
        <h4>Recent Logs</h4>
        <pre className="dashboard-widget-detail__logs" tabIndex={0}>
          {logs.length
            ? logs.map((log) => `[${log.time || '--'}] ${log.message}`).join('\n')
            : 'No logs available'}
        </pre>
      </div>
      <div className="dashboard-widget-detail__actions">
        <Link className="dashboard-link-button" to={linkTarget}>
          Open {widget.type === 'mcp' ? 'MCP server' : 'Agent'}
        </Link>
        <button type="button" className="dashboard-link-button" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const {
    status,
    widgets,
    alerts,
    schedules,
    workflows,
    runs,
    inboxMessages,
    inboxUnreadCount,
    connected,
    streamReady,
    collectionState,
    retryRuns, retrySchedules, retryInbox,
  } = useDashboardData();

  const [widgetFilter, setWidgetFilter] = useState('all');
  const [widgetSearch, setWidgetSearch] = useState('');
  const [expandedWidgetId, setExpandedWidgetId] = useState(null);
  const [showRuntimeDetails, setShowRuntimeDetails] = useState(false);

  const runtimeWidgets = widgets.filter((w) => w.type === 'agent' || w.type === 'mcp');
  const loops = status?.loops ?? EMPTY_LIST;
  const backlog = status?.backlog ?? EMPTY_LIST;

  const activeRuns = useMemo(
    () => runs.filter((r) => r.status === 'queued' || r.status === 'running'),
    [runs],
  );

  const pendingReview = useMemo(
    () => runs.filter((r) => r.status === 'paused' && r.waiting_node_id),
    [runs],
  );

  const enabledSchedules = useMemo(
    () => schedules.filter((s) => s.enabled !== false && s.paused !== true),
    [schedules],
  );

  const upcomingSchedules = useMemo(
    () => [...enabledSchedules].sort((a, b) => {
      const left = Date.parse(a.next_run);
      const right = Date.parse(b.next_run);
      return (Number.isFinite(left) ? left : Infinity) - (Number.isFinite(right) ? right : Infinity);
    }),
    [enabledSchedules],
  );

  const availableResources = useMemo(
    () => runtimeWidgets.filter((w) => isHealthyResourceStatus(widgetStatus(w))),
    [runtimeWidgets],
  );

  const filteredWidgets = useMemo(() => {
    return runtimeWidgets.filter((w) => {
      const ns = normalizeStatus(widgetStatus(w));
      const matchesFilter = widgetFilter === 'all' || ns === widgetFilter;
      const haystack = `${widgetLabel(w)} ${widgetDescription(w)} ${w.type}`.toLowerCase();
      const matchesSearch = !widgetSearch.trim() || haystack.includes(widgetSearch.trim().toLowerCase());
      return matchesFilter && matchesSearch;
    });
  }, [runtimeWidgets, widgetFilter, widgetSearch]);

  const handleToggleWidget = useCallback((id) => {
    setExpandedWidgetId((prev) => (prev === id ? null : id));
  }, []);

  const streamStateLabel = !connected
    ? 'Reconnecting'
    : !streamReady
      ? 'Connecting'
      : 'Live';

  const runtimeLabel = status?.progenitor_running
    ? 'Running'
    : status === null
      ? 'Awaiting status'
      : 'Stopped';

  const pendingApprovals = inboxMessages.filter(
    (m) => m.requires_approval && !m.archived && m.decision == null,
  );
  const unavailableResources = runtimeWidgets.filter((w) => isUnhealthyResourceStatus(widgetStatus(w)));
  const unreadAlerts = alerts.filter((alert) => !alert.viewed);
  const attentionReady = status !== null && collectionState.runs.loaded && collectionState.inbox.loaded;

  return (
    <div className="dashboard-page">
      {/* Header */}
      <header className="dashboard-header">
        <div className="dashboard-header__intro">
          <span className="dashboard-panel__eyebrow">Your workspace, at a glance</span>
          <h1>Workspace overview</h1>
          <p>Execution, reviews, and runtime health in one place.</p>
        </div>
        <div className="dashboard-header__actions">
          <div className="dashboard-header__signals" aria-label="Connection and runtime status">
            <span className="dashboard-header__signal">
              <span className={`dashboard-status-dot${streamReady ? ' dashboard-status-dot--live' : ' dashboard-status-dot--reconnecting'}`} aria-hidden="true" />
              {streamStateLabel === 'Live' ? 'Live updates' : streamStateLabel}
            </span>
            <span className="dashboard-header__signal">
              <span className={`dashboard-status-dot${status?.progenitor_running ? ' dashboard-status-dot--running' : status === null ? ' dashboard-status-dot--pending' : ' dashboard-status-dot--stopped'}`} aria-hidden="true" />
              Runtime {runtimeLabel.toLowerCase()}
            </span>
          </div>
          <Link className="dashboard-header__inbox" to="/inbox">
            Open inbox <span>{collectionState.inbox.loaded ? inboxUnreadCount : '…'} unread</span>
          </Link>
        </div>
      </header>

      {/* Metrics */}
      <section className="dashboard-metrics" aria-label="Key metrics">
        <MetricCard
          label="Active workflow runs"
          icon="M8 5l11 7-11 7V5Z"
          value={collectionState.runs.loaded ? activeRuns.length : '—'}
          detail={collectionState.runs.error ? 'Run data unavailable' : !collectionState.runs.loaded || collectionState.runs.loading ? 'Syncing recent runs…' : `${runs.length} runs in recent history`}
        />
        <MetricCard
          label="Pending review"
          icon="M9 5H5v15h14V5h-4M9 3h6v4H9V3ZM8 13l3 3 5-6"
          value={collectionState.runs.loaded && collectionState.inbox.loaded ? pendingReview.length + pendingApprovals.length : '—'}
          detail={attentionReady ? `${pendingReview.length} workflow · ${pendingApprovals.length} inbox` : 'Waiting for review data…'}
        />
        <MetricCard
          label="Enabled schedules"
          icon="M5 5h14v15H5V5ZM8 3v4m8-4v4M5 10h14m-10 4h2m3 0h2"
          value={collectionState.schedules.loaded ? enabledSchedules.length : '—'}
          detail={collectionState.schedules.error ? 'Schedule data unavailable' : !collectionState.schedules.loaded || collectionState.schedules.loading ? 'Syncing schedules…' : `${schedules.length} configured schedules`}
        />
        <MetricCard
          label="Available resources"
          icon="M4 4h16v6H4V4Zm0 10h16v6H4v-6Zm3-7h1m-1 10h1"
          value={status ? availableResources.length : '—'}
          detail={status ? `${runtimeWidgets.length} agents & MCP servers` : 'Waiting for runtime…'}
        />
      </section>

      {/* Main layout */}
      <div className="dashboard-layout">
        <div className="dashboard-main-column">
          {/* Recent workflow runs */}
          <section className="dashboard-panel" aria-label="Recent workflow runs">
            <div className="dashboard-panel__bar">
              <div>
                <span className="dashboard-panel__eyebrow">Workflows</span>
                <h2>Recent workflow runs</h2>
              </div>
              <span className="dashboard-panel__count">
                {!collectionState.runs.loaded || collectionState.runs.loading ? '…' : runs.length}
              </span>
            </div>
            <div className="dashboard-panel__body">
              <InlineFailure failure={collectionState.runs.error} operation="Load workflow runs" onRetry={retryRuns} />
              {(!collectionState.runs.loaded || collectionState.runs.loading) && !runs.length && !collectionState.runs.error ? (
                <EmptyState small>Loading workflow runs…</EmptyState>
              ) : runs.length === 0 && collectionState.runs.error ? null : runs.length === 0 ? (
                <EmptyState small>
                  No recent workflow runs.
                  <br />
                  <Link className="dashboard-link" to="/workflows">Open Workflows →</Link>
                </EmptyState>
              ) : (
                <div className="dashboard-run-list" role="list">
                  {runs.slice(0, 6).map((run) => (
                    <div key={run.run_id} className="dashboard-run-row" role="listitem">
                      <div className="dashboard-run-row__main">
                        <strong>
                          <Link className="dashboard-link" to={`/workflows?path=${encodeURIComponent(`/workflows/${run.workflow_id}`)}`}>
                            {workflows.find((w) => w.id === run.workflow_id)?.title || run.workflow_id || 'Untitled'}
                          </Link>
                        </strong>
                        <small>
                          {run.run_id}
                          {run.last_error ? ` · ${run.last_error}` : ''}
                        </small>
                      </div>
                      <RunStatusPill status={run.status} />
                      <span className="dashboard-run-row__time">{formatRelativeTime(run.updated_at || run.queued_at)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>

          {/* Runtime inventory */}
          <section id="dashboard-runtime-inventory" className="dashboard-panel" aria-label="Runtime inventory">
            <div className="dashboard-panel__bar">
              <div>
                <span className="dashboard-panel__eyebrow">Inventory</span>
                <h2>Runtime resources</h2>
              </div>
              <span className="dashboard-panel__count">{filteredWidgets.length}</span>
            </div>
            <div className="dashboard-panel__body">
              <div className="dashboard-directory-toolbar">
                <label className="dashboard-search">
                  <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
                    <circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" strokeWidth="1.4" />
                    <path d="M11 11l3 3" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
                  </svg>
                  <input
                    type="text"
                    placeholder="Search resources..."
                    value={widgetSearch}
                    onChange={(e) => setWidgetSearch(e.target.value)}
                    aria-label="Search runtime resources"
                  />
                </label>
                <div className="dashboard-segmented-filter" role="group" aria-label="Resource status filter">
                  {['all', 'ready', 'running', 'offline', 'error', 'disabled'].map((value) => (
                    <button
                      key={value}
                      type="button"
                      className={widgetFilter === value ? 'is-active' : ''}
                      onClick={() => setWidgetFilter(value)}
                      aria-pressed={widgetFilter === value}
                    >
                      {value}
                    </button>
                  ))}
                </div>
              </div>

              <div className="dashboard-widget-list" role="list">
                {status === null ? (
                  <EmptyState small>Waiting for runtime resources…</EmptyState>
                ) : filteredWidgets.length === 0 ? (
                  <EmptyState small>
                    {widgetSearch.trim()
                      ? 'No matching resources'
                      : widgetFilter === 'all' ? 'No runtime resources registered' : `No ${widgetFilter} resources`}
                  </EmptyState>
                ) : (
                  filteredWidgets.map((widget) => {
                    const isExpanded = expandedWidgetId === widget.id;
                    return (
                      <div key={widget.id} role="listitem">
                        <button
                          type="button"
                          className="dashboard-widget-row"
                          onClick={() => handleToggleWidget(widget.id)}
                          aria-expanded={isExpanded}
                          aria-controls={isExpanded ? 'dashboard-resource-details' : undefined}
                        >
                          <span className={`dashboard-widget-row__type dashboard-widget-row__type--${widget.type}`}>
                            {widget.type === 'mcp' ? 'MCP' : 'AGT'}
                          </span>
                          <span className="dashboard-widget-row__main">
                            <strong>{widgetLabel(widget)}</strong>
                            <small>{widgetDescription(widget)}</small>
                          </span>
                          <StatusPill status={widgetStatus(widget)} />
                          <span className="dashboard-widget-row__logs">
                            {(widget.logs || widget.data?.logs || []).length} logs
                          </span>
                        </button>
                        {isExpanded && (
                          <WidgetDetail
                            widget={widget}
                            onClose={() => setExpandedWidgetId(null)}
                          />
                        )}
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          </section>
        </div>

        {/* Right rail */}
        <div className="dashboard-rail">
          {/* Needs attention */}
          <section className="dashboard-panel" aria-label="Needs attention">
            <div className="dashboard-panel__bar">
              <div>
                <span className="dashboard-panel__eyebrow">Attention</span>
                <h2>Needs attention</h2>
              </div>
              <span className="dashboard-panel__count">
                {attentionReady ? unreadAlerts.length + pendingReview.length + pendingApprovals.length + unavailableResources.length : '…'}
              </span>
            </div>
            <div className="dashboard-panel__body">
              <InlineFailure failure={collectionState.runs.error} operation="Load workflow reviews" onRetry={retryRuns} />
              <InlineFailure failure={collectionState.inbox.error} operation="Load review messages" onRetry={retryInbox} />
              {unreadAlerts.length === 0 && pendingReview.length === 0 && pendingApprovals.length === 0 && unavailableResources.length === 0 ? (
                !collectionState.runs.error && !collectionState.inbox.error && <EmptyState small>
                  {attentionReady ? 'Nothing needs attention right now' : 'Waiting for review data…'}
                </EmptyState>
              ) : (
                <div className="dashboard-attention-group">
                  {unavailableResources.length > 0 && (
                    <>
                      <h4>Unavailable resources ({unavailableResources.length})</h4>
                      {unavailableResources.slice(0, 4).map((widget) => (
                        <button
                          type="button"
                          className="dashboard-attention-resource"
                          key={widget.id}
                          onClick={() => {
                            setWidgetFilter('all');
                            setWidgetSearch('');
                            setExpandedWidgetId(widget.id);
                            document.getElementById('dashboard-runtime-inventory')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
                          }}
                        >
                          <span>{widgetLabel(widget)}</span>
                          <StatusPill status={widgetStatus(widget)} />
                        </button>
                      ))}
                    </>
                  )}
                  {unreadAlerts.length > 0 && (
                    <>
                      <h4>Alerts ({unreadAlerts.length})</h4>
                      {unreadAlerts.slice(0, 5).map((alert) => (
                        <MiniRow key={alert.id}>
                          <strong>{alert.title || 'Alert'}</strong>
                          <StatusPill status={alert.type || 'info'} />
                        </MiniRow>
                      ))}
                    </>
                  )}
                  {pendingReview.length > 0 && (
                    <>
                      <h4>Waiting runs ({pendingReview.length})</h4>
                      {pendingReview.slice(0, 5).map((run) => (
                        <MiniRow key={run.run_id}>
                          <strong>
                            <Link className="dashboard-link" to={`/workflows?path=${encodeURIComponent(`/workflows/${run.workflow_id}`)}`}>
                              {workflows.find((w) => w.id === run.workflow_id)?.title || run.workflow_id || 'Workflow run'}
                            </Link>
                          </strong>
                          <RunStatusPill status={run.status} />
                        </MiniRow>
                      ))}
                    </>
                  )}
                  {pendingApprovals.length > 0 && (
                    <>
                      <h4>Inbox approvals ({pendingApprovals.length})</h4>
                      {pendingApprovals.slice(0, 5).map((msg) => (
                        <MiniRow key={msg.id} stacked>
                          <Link className="dashboard-link" to={`/inbox/${encodeURIComponent(msg.id)}`}>
                            <strong>{msg.title || msg.id}</strong>
                          </Link>
                          <small>{msg.body ? msg.body.slice(0, 80) : 'Approval required'}</small>
                        </MiniRow>
                      ))}
                    </>
                  )}
                </div>
              )}
            </div>
          </section>

          {/* Upcoming schedules */}
          <section className="dashboard-panel" aria-label="Upcoming schedules">
            <div className="dashboard-panel__bar">
              <div>
                <span className="dashboard-panel__eyebrow">Schedules</span>
                <h2>Upcoming</h2>
              </div>
              <span className="dashboard-panel__count">
                {!collectionState.schedules.loaded || collectionState.schedules.loading ? '…' : upcomingSchedules.length}
              </span>
            </div>
            <div className="dashboard-panel__body">
              <InlineFailure failure={collectionState.schedules.error} operation="Load schedules" onRetry={retrySchedules} />
              {(!collectionState.schedules.loaded || collectionState.schedules.loading) && !schedules.length && !collectionState.schedules.error ? (
                <EmptyState small>Loading schedules…</EmptyState>
              ) : upcomingSchedules.length === 0 && collectionState.schedules.error ? null : upcomingSchedules.length === 0 ? (
                <EmptyState small>
                  {schedules.length ? 'No enabled schedules.' : 'No schedules configured.'}
                  <br />
                  <Link className="dashboard-link" to="/schedules">Open Schedules →</Link>
                </EmptyState>
              ) : (
                upcomingSchedules.slice(0, 8).map((s) => (
                  <MiniRow key={s.id}>
                    <Link className="dashboard-link" to={`/schedules/${encodeURIComponent(s.id)}`}>
                      <strong>{s.name || s.id}</strong>
                    </Link>
                    <small title={s.schedule || s.cron_expression || undefined}>
                      {Number.isFinite(Date.parse(s.next_run))
                        ? new Date(s.next_run).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
                        : s.schedule || s.cron_expression || 'Next run pending'}
                    </small>
                  </MiniRow>
                ))
              )}
            </div>
          </section>

          {/* Current execution */}
          <section className="dashboard-panel" aria-label="Current execution">
            <div className="dashboard-panel__bar">
              <div>
                <span className="dashboard-panel__eyebrow">Execution</span>
                <h2>Current activity</h2>
              </div>
              <span className="dashboard-panel__count">{loops.length + backlog.length}</span>
            </div>
            <div className="dashboard-panel__body">
              {status === null ? (
                <EmptyState small>Waiting for runtime activity…</EmptyState>
              ) : loops.length === 0 && backlog.length === 0 ? (
                <EmptyState small>No active execution</EmptyState>
              ) : (
                <div className="dashboard-attention-group">
                  {loops.length > 0 && (
                    <>
                      <h4>Runtime loops ({loops.length})</h4>
                      {loops.map((loop, i) => (
                        <MiniRow key={`${loop.name || i}`} stacked>
                          <strong>{loop.name || `Loop ${i + 1}`}</strong>
                          <small>{loop.current_task || loop.status || 'active'}</small>
                        </MiniRow>
                      ))}
                    </>
                  )}
                  {backlog.length > 0 && (
                    <>
                      <h4>Backlog ({backlog.length})</h4>
                      {backlog.slice(0, 6).map((item, i) => (
                        <MiniRow key={`${item.id || i}`} stacked>
                          <strong>{item.description || item.type || item.id || 'Item'}</strong>
                          <small>{item.status || 'pending'} {item.priority ? `· priority ${item.priority}` : ''}</small>
                        </MiniRow>
                      ))}
                    </>
                  )}
                </div>
              )}
            </div>
          </section>

          {/* Runtime provenance */}
          <section className="dashboard-panel" aria-label="Runtime provenance">
            <button
              type="button"
              className="dashboard-collapsible__trigger"
              onClick={() => setShowRuntimeDetails((p) => !p)}
              aria-expanded={showRuntimeDetails}
            >
              <div>
                <span className="dashboard-panel__eyebrow">Diagnostics</span>
                <span className="dashboard-collapsible__title">Runtime provenance</span>
              </div>
              <span className="dashboard-collapsible__chevron">{showRuntimeDetails ? '▾' : '▸'}</span>
            </button>
            {showRuntimeDetails && (
              <div className="dashboard-kv-grid">
                <span>Thread</span><strong>{status?.thread_id || '--'}</strong>
                <span>PID</span><strong>{status?.pid || '--'}</strong>
                <span>API version</span><strong>{status?.version || '--'}</strong>
                <span>Messages</span><strong>{status?.messages_count ?? 0}</strong>
                <span>Cycles</span><strong>{status?.cycle_count ?? 0}</strong>
                <span>Uptime</span><strong>{formatUptime(status?.uptime_seconds)}</strong>
                <span>Memory</span><strong>{typeof status?.memory_mb === 'number' ? `${status.memory_mb.toFixed(1)} MB` : '--'}</strong>
                <span>Commit</span><strong>{status?.progenitor_commit || '--'}</strong>
                <span>Started</span><strong>{status?.progenitor_started_at ? formatRelativeTime(status.progenitor_started_at) : '--'}</strong>
                <span>Model</span><strong>{status?.llm_model || '--'}</strong>
                <span>Reasoning</span>
                <strong>
                  {status?.llm_reasoning_enabled
                    ? status?.llm_reasoning_effort || 'on'
                    : 'off'}
                </strong>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
