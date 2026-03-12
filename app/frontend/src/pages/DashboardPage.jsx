import { useEffect, useState, useCallback } from 'react';
import { useApi } from '../hooks/useApi';
import { useStatusSSE } from '../hooks/useSSE';
import Header from '../components/layout/Header';

function DashboardPage() {
  const { connectionState, getWidgets } = useApi();
  const [status, setStatus] = useState(null);
  const [widgets, setWidgets] = useState([]);

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
                <strong>Run ID</strong>
                <span>{status?.run_id || '--'}</span>
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
      </main>
    </div>
  );
}

export default DashboardPage;
