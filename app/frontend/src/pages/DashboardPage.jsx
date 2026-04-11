import { useEffect, useState, useCallback } from 'react';
import { useApi } from '../hooks/useApi';
import { useStatusSSE } from '../hooks/useSSE';
import Header from '../components/layout/Header';
import { DashboardSidebarSections } from '../components/layout/Sidebar';

function DashboardPage() {
  const { connectionState, getWidgets } = useApi();
  const [status, setStatus] = useState(null);
  const [widgets, setWidgets] = useState([]);
  const [widgetFilter, setWidgetFilter] = useState('all');
  const [widgetSearch, setWidgetSearch] = useState('');
  const [selectedWidget, setSelectedWidget] = useState(null);

  const loadWidgets = useCallback(async () => {
    try {
      const response = await getWidgets();
      setWidgets(response.items || response.widgets || response || []);
    } catch (error) {
      console.warn('[DashboardPage] Failed to load widgets:', error);
    }
  }, [getWidgets]);

  useEffect(() => {
    void (async () => {
      await loadWidgets();
    })();
  }, [loadWidgets]);

  const handleStatusEvent = useCallback((event) => {
    if (!event?.data) {
      return;
    }

    if (event.type === 'connected' || event.type === 'status' || event.type === 'heartbeat') {
      setStatus(event.data);
      loadWidgets();
    }
  }, [loadWidgets]);

  useStatusSSE(handleStatusEvent);

  const agentWidgets = widgets.filter((widget) => widget.type === 'agent');
  const mcpWidgets = widgets.filter((widget) => widget.type === 'mcp');
  const scheduleWidgets = widgets.filter((widget) => widget.type === 'schedule');
  const runtimeWidgets = widgets.filter((widget) => widget.type === 'agent' || widget.type === 'mcp');
  const filteredRuntimeWidgets = runtimeWidgets.filter((widget) => {
    const widgetStatus = widget.status || widget.data?.agentStatus || 'idle';
    const normalizedStatus = widgetStatus === 'stopped' ? 'offline' : widgetStatus;
    const label = widget.title || widget.data?.title || widget.id;
    const description = widget.summary || widget.data?.summaryState || widget.data?.description || '';
    const matchesFilter = widgetFilter === 'all' || normalizedStatus === widgetFilter;
    const matchesSearch = !widgetSearch
      || label.toLowerCase().includes(widgetSearch.toLowerCase())
      || description.toLowerCase().includes(widgetSearch.toLowerCase());
    return matchesFilter && matchesSearch;
  });

  const runtimeStats = {
    total: runtimeWidgets.length,
    ready: runtimeWidgets.filter((widget) => (widget.status || widget.data?.agentStatus || 'idle') === 'ready').length,
    running: runtimeWidgets.filter((widget) => ['running', 'busy', 'executing'].includes(widget.status || widget.data?.agentStatus || 'idle')).length,
    offline: runtimeWidgets.filter((widget) => ['offline', 'stopped'].includes(widget.status || widget.data?.agentStatus || 'idle')).length,
  };

  const formatUptime = (seconds) => {
    if (!seconds) return '--';
    if (seconds < 60) return `${Math.floor(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  };

  return (
    <div className="dashboard-container">
      <Header connectionState={connectionState} />
      <main className="dashboard-main">
        <section className="stats-grid">
          <div className="stat-card primary">
            <div className="stat-icon">🧠</div>
            <div className="stat-content">
              <div className="stat-value">{status?.progenitor_running ? 'Running' : 'Stopped'}</div>
              <div className="stat-label">Agent Status</div>
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-icon">🔄</div>
            <div className="stat-content">
              <div className="stat-value">{status?.cycle_count ?? 0}</div>
              <div className="stat-label">Cycle Count</div>
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-icon">🤖</div>
            <div className="stat-content">
              <div className="stat-value">{agentWidgets.length}</div>
              <div className="stat-label">Agent Widgets</div>
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-icon">🧰</div>
            <div className="stat-content">
              <div className="stat-value">{mcpWidgets.length}</div>
              <div className="stat-label">MCP Widgets</div>
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-icon">⏰</div>
            <div className="stat-content">
              <div className="stat-value">{scheduleWidgets.length}</div>
              <div className="stat-label">Schedules</div>
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-icon">⏱️</div>
            <div className="stat-content">
              <div className="stat-value">{formatUptime(status?.uptime_seconds)}</div>
              <div className="stat-label">Uptime</div>
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-icon">💾</div>
            <div className="stat-content">
              <div className="stat-value">{status?.memory_mb?.toFixed(1) || '--'} MB</div>
              <div className="stat-label">Memory</div>
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-icon">💬</div>
            <div className="stat-content">
              <div className="stat-value">{status?.messages_count ?? 0}</div>
              <div className="stat-label">Messages</div>
            </div>
          </div>
        </section>

        <section className="dashboard-grid">
          <div className="dashboard-panel agents-panel">
            <div className="panel-header">
              <h3>Runtime Widgets</h3>
              <span className="badge">{widgets.length}</span>
            </div>
            <div className="panel-body">
              {widgets.length > 0 ? (
                widgets.slice(0, 8).map((widget) => (
                  <div key={widget.id} className="agent-item">
                    <span className="agent-icon">{widget.type === 'mcp' ? '🧰' : widget.type === 'schedule' ? '⏰' : '🤖'}</span>
                    <span className="agent-name">{widget.data?.title || widget.id}</span>
                    <span className={`agent-status ${widget.data?.agentStatus || widget.data?.status || 'idle'}`}>
                      {widget.data?.agentStatus || widget.data?.status || 'idle'}
                    </span>
                  </div>
                ))
              ) : (
                <div className="empty-state">
                  <span className="empty-icon">📦</span>
                  <span>No widgets loaded</span>
                </div>
              )}
            </div>
          </div>

          <div className="dashboard-panel activity-panel">
            <div className="panel-header">
              <h3>System Snapshot</h3>
            </div>
            <div className="panel-body timeline">
              <div className="timeline-item">
                <strong>Thread ID</strong>
                <span>{status?.thread_id || '--'}</span>
              </div>
              <div className="timeline-item">
                <strong>Last Active</strong>
                <span>{status?.last_active || '--'}</span>
              </div>
              <div className="timeline-item">
                <strong>PID</strong>
                <span>{status?.pid || '--'}</span>
              </div>
              <div className="timeline-item">
                <strong>Version</strong>
                <span>{status?.version || '--'}</span>
              </div>
            </div>
          </div>
        </section>

        <section className="dashboard-panel widget-directory-panel">
          <div className="panel-header">
            <h3>Widget Directory</h3>
            <span className="badge">{filteredRuntimeWidgets.length}</span>
          </div>
          <div className="panel-body widget-directory-body">
            <div className="agents-toolbar dashboard-toolbar">
              <div className="search-box">
                <span className="search-icon">🔍</span>
                <input
                  type="text"
                  placeholder="Search agent and MCP widgets..."
                  className="search-input"
                  value={widgetSearch}
                  onChange={(event) => setWidgetSearch(event.target.value)}
                />
              </div>
              <div className="filter-buttons">
                {['all', 'ready', 'running', 'offline'].map((value) => (
                  <button
                    key={value}
                    className={`filter-btn ${widgetFilter === value ? 'active' : ''}`}
                    onClick={() => setWidgetFilter(value)}
                  >
                    {value.charAt(0).toUpperCase() + value.slice(1)}
                  </button>
                ))}
              </div>
            </div>

            <div className="agents-summary dashboard-agent-summary">
              <div className="summary-card">
                <span className="summary-icon">🧩</span>
                <div className="summary-data">
                  <span className="summary-value">{runtimeStats.total}</span>
                  <span className="summary-label">Widgets</span>
                </div>
              </div>
              <div className="summary-card success">
                <span className="summary-icon">✓</span>
                <div className="summary-data">
                  <span className="summary-value">{runtimeStats.ready}</span>
                  <span className="summary-label">Ready</span>
                </div>
              </div>
              <div className="summary-card warning">
                <span className="summary-icon">⏳</span>
                <div className="summary-data">
                  <span className="summary-value">{runtimeStats.running}</span>
                  <span className="summary-label">Running</span>
                </div>
              </div>
              <div className="summary-card info">
                <span className="summary-icon">⏹️</span>
                <div className="summary-data">
                  <span className="summary-value">{runtimeStats.offline}</span>
                  <span className="summary-label">Offline</span>
                </div>
              </div>
            </div>

            <div className="widget-card-grid">
              {filteredRuntimeWidgets.length > 0 ? (
                filteredRuntimeWidgets.map((widget) => {
                  const widgetStatus = widget.status || widget.data?.agentStatus || 'idle';
                  return (
                    <button
                      key={widget.id}
                      type="button"
                      className="widget-card"
                      onClick={() => setSelectedWidget(widget)}
                    >
                      <div className="widget-card-header">
                        <span className="agent-icon">{widget.type === 'mcp' ? '🧰' : '🤖'}</span>
                        <span className={`agent-status ${widgetStatus}`}>{widgetStatus}</span>
                      </div>
                      <div className="widget-card-title">
                        {widget.title || widget.data?.title || widget.id}
                      </div>
                      <div className="widget-card-description">
                        {widget.summary || widget.data?.summaryState || widget.data?.description || 'No description'}
                      </div>
                      <div className="widget-card-footer">
                        <span>{widget.type.toUpperCase()}</span>
                        <span>{widget.logs?.length || widget.data?.logs?.length || 0} logs</span>
                      </div>
                    </button>
                  );
                })
              ) : (
                <div className="empty-state">
                  <span className="empty-icon">🤖</span>
                  <span>No matching widgets found</span>
                </div>
              )}
            </div>
          </div>
        </section>

        <section className="dashboard-grid dashboard-grid-expanded">
          <DashboardSidebarSections status={status} />
        </section>
      </main>

      {selectedWidget && (
        <>
          <aside className="agent-drawer dashboard-agent-drawer">
            <div className="drawer-header">
              <div className="drawer-title">
                <span className="drawer-icon">{selectedWidget.type === 'mcp' ? '🧰' : '🤖'}</span>
                <h2>{selectedWidget.title || selectedWidget.data?.title || selectedWidget.id}</h2>
              </div>
              <button className="drawer-close" onClick={() => setSelectedWidget(null)}>×</button>
            </div>
            <div className="drawer-content">
              <div className="drawer-section">
                <h4>Status</h4>
                <span className={`agent-status ${selectedWidget.status || selectedWidget.data?.agentStatus || 'idle'}`}>
                  {selectedWidget.status || selectedWidget.data?.agentStatus || 'idle'}
                </span>
              </div>
              <div className="drawer-section">
                <h4>Summary</h4>
                <p>{selectedWidget.summary || selectedWidget.data?.summaryState || selectedWidget.data?.description || 'No summary available'}</p>
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
          <div className="drawer-backdrop" onClick={() => setSelectedWidget(null)} />
        </>
      )}
    </div>
  );
}

export default DashboardPage;
