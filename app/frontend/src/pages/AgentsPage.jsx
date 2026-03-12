import { useState, useEffect, useCallback } from 'react';
import { useApi } from '../hooks/useApi';
import { useStatusSSE } from '../hooks/useSSE';
import Header from '../components/layout/Header';

function AgentsPage() {
  const { connectionState, getWidgets } = useApi();
  const [widgets, setWidgets] = useState([]);
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [view, setView] = useState('grid');
  const [selectedWidget, setSelectedWidget] = useState(null);

  const loadWidgets = useCallback(async () => {
    try {
      const response = await getWidgets();
      const allWidgets = response.items || response.widgets || response || [];
      setWidgets(allWidgets.filter((widget) => widget.type === 'agent' || widget.type === 'mcp'));
    } catch (error) {
      console.warn('[AgentsPage] Failed to load widgets:', error);
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
      loadWidgets();
    }
  }, [loadWidgets]);

  useStatusSSE(handleStatusEvent);

  const filteredWidgets = widgets.filter((widget) => {
    const status = widget.status || widget.data?.agentStatus || 'idle';
    const matchesFilter = filter === 'all' || status === filter || (filter === 'offline' && status === 'stopped');
    const label = widget.data?.title || widget.id;
    const description = widget.summary || widget.data?.summaryState || widget.data?.description || '';
    const matchesSearch = !search ||
      label.toLowerCase().includes(search.toLowerCase()) ||
      description.toLowerCase().includes(search.toLowerCase());
    return matchesFilter && matchesSearch;
  });

  const stats = {
    total: widgets.length,
    ready: widgets.filter((widget) => (widget.status || widget.data?.agentStatus || 'idle') === 'ready').length,
    busy: widgets.filter((widget) => ['running', 'busy', 'executing'].includes(widget.status || widget.data?.agentStatus || 'idle')).length,
    offline: widgets.filter((widget) => ['offline', 'stopped'].includes(widget.status || widget.data?.agentStatus || 'idle')).length,
  };

  return (
    <div className="dashboard-container">
      <Header connectionState={connectionState} />
      <main className="agents-main">
        <section className="agents-toolbar">
          <div className="search-box">
            <span className="search-icon">🔍</span>
            <input
              type="text"
              placeholder="Search agent and MCP widgets..."
              className="search-input"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          <div className="filter-buttons">
            {['all', 'ready', 'running', 'offline'].map((value) => (
              <button
                key={value}
                className={`filter-btn ${filter === value ? 'active' : ''}`}
                onClick={() => setFilter(value)}
              >
                {value.charAt(0).toUpperCase() + value.slice(1)}
              </button>
            ))}
          </div>
          <div className="view-toggle">
            <button className={`view-btn ${view === 'grid' ? 'active' : ''}`} onClick={() => setView('grid')}>◫</button>
            <button className={`view-btn ${view === 'list' ? 'active' : ''}`} onClick={() => setView('list')}>☰</button>
          </div>
        </section>

        <section className="agents-summary">
          <div className="summary-card">
            <span className="summary-icon">🧩</span>
            <div className="summary-data">
              <span className="summary-value">{stats.total}</span>
              <span className="summary-label">Widgets</span>
            </div>
          </div>
          <div className="summary-card success">
            <span className="summary-icon">✓</span>
            <div className="summary-data">
              <span className="summary-value">{stats.ready}</span>
              <span className="summary-label">Ready</span>
            </div>
          </div>
          <div className="summary-card warning">
            <span className="summary-icon">⏳</span>
            <div className="summary-data">
              <span className="summary-value">{stats.busy}</span>
              <span className="summary-label">Running</span>
            </div>
          </div>
          <div className="summary-card info">
            <span className="summary-icon">⏹️</span>
            <div className="summary-data">
              <span className="summary-value">{stats.offline}</span>
              <span className="summary-label">Offline</span>
            </div>
          </div>
        </section>

        <section className={`agents-${view}`} id="agents-container">
          {filteredWidgets.length > 0 ? (
            filteredWidgets.map((widget) => {
              const status = widget.status || widget.data?.agentStatus || 'idle';
              return (
                <div
                  key={widget.id}
                  className="agent-card"
                  onClick={() => setSelectedWidget(widget)}
                >
                  <div className="agent-card-header">
                    <span className="agent-icon">{widget.type === 'mcp' ? '🧰' : '🤖'}</span>
                    <span className={`agent-status-badge ${status}`}>{status}</span>
                  </div>
                  <div className="agent-card-body">
                    <h3 className="agent-name">{widget.title || widget.data?.title || widget.id}</h3>
                    <p className="agent-description">{widget.summary || widget.data?.summaryState || widget.data?.description || 'No description'}</p>
                  </div>
                  <div className="agent-card-footer">
                    <span className="agent-stat">{widget.type.toUpperCase()}</span>
                    <span className="agent-stat">{widget.logs?.length || widget.data?.logs?.length || 0} logs</span>
                  </div>
                </div>
              );
            })
          ) : (
            <div className="empty-state">
              <span className="empty-icon">🤖</span>
              <span>No matching widgets found</span>
            </div>
          )}
        </section>

        {selectedWidget && (
          <>
            <aside className="agent-drawer">
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
                  <span className={`status-badge ${selectedWidget.status || selectedWidget.data?.agentStatus || 'idle'}`}>
                    {selectedWidget.status || selectedWidget.data?.agentStatus || 'idle'}
                  </span>
                </div>
                <div className="drawer-section">
                  <h4>Summary</h4>
                  <p>{selectedWidget.summary || selectedWidget.data?.summaryState || 'No summary available'}</p>
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
      </main>
    </div>
  );
}

export default AgentsPage;
