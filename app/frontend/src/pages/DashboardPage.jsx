import { useMemo, useState } from 'react';
import { useSelector } from 'react-redux';
import { useChromeRuntime } from '../context/ChromeRuntimeContext';
import { selectUiWidgets } from '../store/uiDataSlice';

const EMPTY_LIST = [];

function widgetStatus(widget) {
  return widget.status || widget.data?.agentStatus || widget.data?.status || 'idle';
}

function widgetLabel(widget) {
  return widget.title || widget.data?.title || widget.id;
}

function widgetDescription(widget) {
  return widget.summary || widget.data?.summaryState || widget.data?.description || 'No description';
}

function formatUptime(seconds) {
  if (!seconds) return '--';
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

function formatLastActive(value) {
  if (!value) return '--';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function normalizeStatus(value) {
  if (value === 'stopped') return 'offline';
  if (['busy', 'executing'].includes(value)) return 'running';
  return value || 'idle';
}

function StatusPill({ status }) {
  const normalized = normalizeStatus(status);
  return <span className={`dashboard-status-pill dashboard-status-pill--${normalized}`}>{normalized}</span>;
}

function DashboardMetric({ label, value, detail, tone = 'neutral' }) {
  return (
    <article className={`dashboard-metric dashboard-metric--${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

function MiniList({ items, emptyLabel, renderItem }) {
  return (
    <div className="dashboard-mini-list">
      {items.length ? items.map(renderItem) : <div className="dashboard-empty-row">{emptyLabel}</div>}
    </div>
  );
}

export default function DashboardPage() {
  const { status } = useChromeRuntime();
  const widgets = useSelector(selectUiWidgets);
  const [widgetFilter, setWidgetFilter] = useState('all');
  const [widgetSearch, setWidgetSearch] = useState('');
  const [selectedWidgetId, setSelectedWidgetId] = useState(null);

  const scheduleWidgets = widgets.filter((widget) => widget.type === 'schedule');
  const runtimeWidgets = widgets.filter((widget) => widget.type === 'agent' || widget.type === 'mcp');
  const loops = status?.loops ?? EMPTY_LIST;
  const agents = status?.agents ?? EMPTY_LIST;
  const backlog = status?.backlog ?? EMPTY_LIST;
  const schedule = status?.schedule ?? EMPTY_LIST;

  const runtimeStats = useMemo(() => ({
    total: runtimeWidgets.length,
    ready: runtimeWidgets.filter((widget) => normalizeStatus(widgetStatus(widget)) === 'ready').length,
    running: runtimeWidgets.filter((widget) => normalizeStatus(widgetStatus(widget)) === 'running').length,
    offline: runtimeWidgets.filter((widget) => normalizeStatus(widgetStatus(widget)) === 'offline').length,
  }), [runtimeWidgets]);

  const filteredRuntimeWidgets = useMemo(() => (
    runtimeWidgets.filter((widget) => {
      const normalizedStatus = normalizeStatus(widgetStatus(widget));
      const matchesFilter = widgetFilter === 'all' || normalizedStatus === widgetFilter;
      const haystack = `${widgetLabel(widget)} ${widgetDescription(widget)} ${widget.type}`.toLowerCase();
      const matchesSearch = !widgetSearch.trim() || haystack.includes(widgetSearch.trim().toLowerCase());
      return matchesFilter && matchesSearch;
    })
  ), [runtimeWidgets, widgetFilter, widgetSearch]);

  const selectedWidget = useMemo(
    () => widgets.find((widget) => widget.id === selectedWidgetId) || null,
    [selectedWidgetId, widgets],
  );

  const visibleAgents = agents.length ? agents : runtimeWidgets.map((widget) => ({
    name: widgetLabel(widget),
    status: widgetStatus(widget),
    type: widget.type,
  }));

  const visibleSchedule = schedule.length ? schedule : scheduleWidgets.map((widget) => ({
    time: widget.data?.nextRun || widget.data?.time || '--',
    name: widgetLabel(widget),
  }));

  return (
    <div className="dashboard-page-v2">
      <section className="dashboard-command-strip" aria-label="Runtime dashboard summary">
        <div className="dashboard-command-strip__identity">
          <span className={`dashboard-live-dot ${status?.progenitor_running ? 'is-online' : 'is-offline'}`} />
          <div>
            <strong>{status?.progenitor_running ? 'Progenitor running' : 'Progenitor stopped'}</strong>
            <span>{formatLastActive(status?.last_active)}</span>
          </div>
        </div>
        <DashboardMetric label="Cycles" value={status?.cycle_count ?? 0} detail={`${formatUptime(status?.uptime_seconds)} uptime`} tone="blue" />
        <DashboardMetric label="Runtime widgets" value={runtimeStats.total} detail={`${runtimeStats.ready} ready / ${runtimeStats.running} running`} tone="green" />
        <DashboardMetric label="Memory" value={status?.memory_mb?.toFixed(1) || '--'} detail="MB resident" tone="violet" />
        <DashboardMetric label="Backlog" value={backlog.length} detail={`${visibleSchedule.length} scheduled`} tone={backlog.length ? 'amber' : 'neutral'} />
      </section>

      <main className="dashboard-workspace-v2">
        <section className="dashboard-panel-v2 dashboard-panel-v2--directory">
          <div className="dashboard-panel-v2__bar">
            <div>
              <span className="dashboard-panel-v2__eyebrow">Directory</span>
              <strong>Agents and MCP servers</strong>
            </div>
            <span className="dashboard-panel-v2__count">{filteredRuntimeWidgets.length} shown</span>
          </div>

          <div className="dashboard-directory-toolbar">
            <label className="dashboard-search">
              <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
                <circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" strokeWidth="1.4" />
                <path d="M11 11l3 3" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
              </svg>
              <input
                type="text"
                placeholder="Search widgets..."
                value={widgetSearch}
                onChange={(event) => setWidgetSearch(event.target.value)}
              />
            </label>
            <div className="dashboard-segmented-filter" role="group" aria-label="Widget status filter">
              {['all', 'ready', 'running', 'offline'].map((value) => (
                <button
                  key={value}
                  type="button"
                  className={widgetFilter === value ? 'is-active' : ''}
                  onClick={() => setWidgetFilter(value)}
                >
                  {value}
                </button>
              ))}
            </div>
          </div>

          <div className="dashboard-widget-table" role="list">
            {filteredRuntimeWidgets.length ? filteredRuntimeWidgets.map((widget) => (
              <button
                key={widget.id}
                type="button"
                className="dashboard-widget-row"
                onClick={() => setSelectedWidgetId(widget.id)}
              >
                <span className={`dashboard-widget-row__type dashboard-widget-row__type--${widget.type}`}>{widget.type === 'mcp' ? 'MCP' : 'AGT'}</span>
                <span className="dashboard-widget-row__main">
                  <strong>{widgetLabel(widget)}</strong>
                  <small>{widgetDescription(widget)}</small>
                </span>
                <StatusPill status={widgetStatus(widget)} />
                <span className="dashboard-widget-row__logs">{widget.logs?.length || widget.data?.logs?.length || 0} logs</span>
              </button>
            )) : <div className="dashboard-empty-row">No matching widgets found</div>}
          </div>
        </section>

        <section className="dashboard-panel-v2 dashboard-panel-v2--health">
          <div className="dashboard-panel-v2__bar">
            <div>
              <span className="dashboard-panel-v2__eyebrow">Runtime</span>
              <strong>System snapshot</strong>
            </div>
          </div>
          <div className="dashboard-kv-grid">
            <span>Thread</span><strong>{status?.thread_id || '--'}</strong>
            <span>PID</span><strong>{status?.pid || '--'}</strong>
            <span>API version</span><strong>{status?.version || '--'}</strong>
            <span>Messages</span><strong>{status?.messages_count ?? 0}</strong>
            {/* progenitor_commit/started_at come from the runtime's health
                document (Postgres) - distinct from the backend's own api_version
                above. Lets a stale process running old code be spotted at a
                glance instead of cross-referencing Postgres by hand. */}
            <span>Commit</span><strong>{status?.progenitor_commit || '--'}</strong>
            <span>Started</span><strong>{formatLastActive(status?.progenitor_started_at)}</strong>
            <span>Model</span><strong>{status?.llm_model || '--'}</strong>
            <span>Reasoning</span>
            <strong>
              {status?.llm_reasoning_enabled
                ? status?.llm_reasoning_effort || 'on'
                : 'off'}
            </strong>
          </div>
        </section>

        <section className="dashboard-panel-v2">
          <div className="dashboard-panel-v2__bar">
            <div>
              <span className="dashboard-panel-v2__eyebrow">Loops</span>
              <strong>Current execution</strong>
            </div>
            <span className="dashboard-panel-v2__count">{loops.length}</span>
          </div>
          <MiniList
            items={loops}
            emptyLabel="No active loops"
            renderItem={(loop, index) => (
              <div key={`${loop.name || 'loop'}-${index}`} className="dashboard-mini-row">
                <strong>{loop.name || `Loop ${index + 1}`}</strong>
                <StatusPill status={loop.status} />
              </div>
            )}
          />
        </section>

        <section className="dashboard-panel-v2 dashboard-panel-v2--agents">
          <div className="dashboard-panel-v2__bar">
            <div>
              <span className="dashboard-panel-v2__eyebrow">Agents</span>
              <strong>Registered runtime</strong>
            </div>
            <span className="dashboard-panel-v2__count">{visibleAgents.length}</span>
          </div>
          <MiniList
            items={visibleAgents.slice(0, 8)}
            emptyLabel="No registered agents"
            renderItem={(agent, index) => (
              <div key={`${agent.name}-${index}`} className="dashboard-mini-row">
                <strong>{agent.name}</strong>
                <StatusPill status={agent.status} />
              </div>
            )}
          />
        </section>

        <section className="dashboard-panel-v2 dashboard-panel-v2--backlog">
          <div className="dashboard-panel-v2__bar">
            <div>
              <span className="dashboard-panel-v2__eyebrow">Backlog</span>
              <strong>Queued work</strong>
            </div>
            <span className="dashboard-panel-v2__count">{backlog.length}</span>
          </div>
          <MiniList
            items={backlog.slice(0, 8)}
            emptyLabel="No backlog items"
            renderItem={(item, index) => (
              <div key={`${item.title}-${index}`} className="dashboard-mini-row dashboard-mini-row--stacked">
                <strong>{item.title}</strong>
                <small>{item.priority || item.status || 'pending'}</small>
              </div>
            )}
          />
        </section>

        <section className="dashboard-panel-v2 dashboard-panel-v2--schedule">
          <div className="dashboard-panel-v2__bar">
            <div>
              <span className="dashboard-panel-v2__eyebrow">Schedule</span>
              <strong>Next runs</strong>
            </div>
            <span className="dashboard-panel-v2__count">{visibleSchedule.length}</span>
          </div>
          <MiniList
            items={visibleSchedule.slice(0, 8)}
            emptyLabel="No scheduled tasks"
            renderItem={(task, index) => (
              <div key={`${task.name}-${index}`} className="dashboard-mini-row">
                <strong>{task.name}</strong>
                <small>{task.time}</small>
              </div>
            )}
          />
        </section>
      </main>

      {selectedWidget ? (
        <>
          <aside className="agent-drawer dashboard-agent-drawer">
            <div className="drawer-header">
              <div className="drawer-title">
                <span className="drawer-icon">{selectedWidget.type === 'mcp' ? 'MCP' : 'AGT'}</span>
                <h2>{widgetLabel(selectedWidget)}</h2>
              </div>
              <button type="button" className="drawer-close" onClick={() => setSelectedWidgetId(null)}>x</button>
            </div>
            <div className="drawer-content">
              <div className="drawer-section">
                <h4>Status</h4>
                <StatusPill status={widgetStatus(selectedWidget)} />
              </div>
              <div className="drawer-section">
                <h4>Summary</h4>
                <p>{widgetDescription(selectedWidget)}</p>
              </div>
              <div className="drawer-section">
                <h4>Recent Logs</h4>
                <pre className="detail-data">
                  {(selectedWidget.logs || selectedWidget.data?.logs || [])
                    .slice(-12)
                    .map((log) => `[${log.time || '--'}] ${log.message}`)
                    .join('\n') || 'No logs available'}
                </pre>
              </div>
            </div>
          </aside>
          <div className="drawer-backdrop" onClick={() => setSelectedWidgetId(null)} />
        </>
      ) : null}
    </div>
  );
}
