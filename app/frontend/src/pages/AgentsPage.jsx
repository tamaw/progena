import { useState, useEffect, useCallback } from 'react';
import { useApi } from '../hooks/useApi';
import { useSSE } from '../hooks/useSSE';
import Header from '../components/layout/Header';

function AgentsPage() {
  const { connectionState, getHealth } = useApi();
  const [agents, setAgents] = useState([]);
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [view, setView] = useState('grid');
  const [selectedAgent, setSelectedAgent] = useState(null);

  useEffect(() => {
    const fetchHealth = async () => {
      try {
        await getHealth();
      } catch (e) {
        // Silently fail
      }
    };

    fetchHealth();
    const interval = setInterval(fetchHealth, 5000);
    return () => clearInterval(interval);
  }, [getHealth]);

  const handleSSEEvent = useCallback((event) => {
    if (!event?.data) return;

    if (event.type === 'connected' || event.type === 'status' || event.type === 'heartbeat') {
      setAgents(event.data.agents || []);
    }
  }, []);

  useSSE(handleSSEEvent);

  const filteredAgents = agents.filter(agent => {
    const matchesFilter = filter === 'all' || agent.status === filter;
    const matchesSearch = !search || 
      agent.name?.toLowerCase().includes(search.toLowerCase()) ||
      agent.description?.toLowerCase().includes(search.toLowerCase());
    return matchesFilter && matchesSearch;
  });

  const stats = {
    total: agents.length,
    ready: agents.filter(a => a.status === 'ready').length,
    busy: agents.filter(a => a.status === 'busy').length,
    offline: agents.filter(a => a.status === 'offline').length,
    totalRequests: agents.reduce((sum, a) => sum + (a.requests || 0), 0),
    totalTokens: agents.reduce((sum, a) => sum + (a.tokens || 0), 0)
  };

  return (
    <div className="dashboard-container">
      <Header connectionState={connectionState} />
      <main className="agents-main">
        {/* Toolbar */}
        <section className="agents-toolbar">
          <div className="search-box">
            <span className="search-icon">🔍</span>
            <input
              type="text"
              placeholder="Search agents..."
              className="search-input"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="filter-buttons">
            {['all', 'ready', 'busy', 'offline'].map(f => (
              <button
                key={f}
                className={`filter-btn ${filter === f ? 'active' : ''}`}
                onClick={() => setFilter(f)}
              >
                {f.charAt(0).toUpperCase() + f.slice(1)}
              </button>
            ))}
          </div>
          <div className="view-toggle">
            <button
              className={`view-btn ${view === 'grid' ? 'active' : ''}`}
              onClick={() => setView('grid')}
              title="Grid View"
            >◫</button>
            <button
              className={`view-btn ${view === 'list' ? 'active' : ''}`}
              onClick={() => setView('list')}
              title="List View"
            >☰</button>
          </div>
        </section>

        {/* Summary Stats */}
        <section className="agents-summary">
          <div className="summary-card">
            <span className="summary-icon">🤖</span>
            <div className="summary-data">
              <span className="summary-value">{stats.total}</span>
              <span className="summary-label">Total Agents</span>
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
              <span className="summary-label">Busy</span>
            </div>
          </div>
          <div className="summary-card info">
            <span className="summary-icon">🎯</span>
            <div className="summary-data">
              <span className="summary-value">{stats.totalRequests}</span>
              <span className="summary-label">Total Requests</span>
            </div>
          </div>
          <div className="summary-card tokens">
            <span className="summary-icon">📊</span>
            <div className="summary-data">
              <span className="summary-value">{stats.totalTokens}</span>
              <span className="summary-label">Total Tokens</span>
            </div>
          </div>
        </section>

        {/* Agents Grid/List */}
        <section className={`agents-${view}`} id="agents-container">
          {filteredAgents.length > 0 ? (
            filteredAgents.map((agent, i) => (
              <div 
                key={i} 
                className="agent-card"
                onClick={() => setSelectedAgent(agent)}
              >
                <div className="agent-card-header">
                  <span className="agent-icon">{agent.icon || '🤖'}</span>
                  <span className={`agent-status-badge ${agent.status}`}>{agent.status}</span>
                </div>
                <div className="agent-card-body">
                  <h3 className="agent-name">{agent.name}</h3>
                  <p className="agent-description">{agent.description || 'No description'}</p>
                </div>
                <div className="agent-card-footer">
                  <span className="agent-stat">📊 {agent.requests || 0} requests</span>
                  <span className="agent-stat">🎯 {agent.tokens || 0} tokens</span>
                </div>
              </div>
            ))
          ) : (
            <div className="empty-state">
              <span className="empty-icon">🤖</span>
              <span>{search ? 'No matching agents found' : 'No agents registered'}</span>
            </div>
          )}
        </section>

        {/* Agent Detail Drawer */}
        {selectedAgent && (
          <>
            <aside className="agent-drawer">
              <div className="drawer-header">
                <div className="drawer-title">
                  <span className="drawer-icon">{selectedAgent.icon || '🤖'}</span>
                  <h2>{selectedAgent.name}</h2>
                </div>
                <button className="drawer-close" onClick={() => setSelectedAgent(null)}>×</button>
              </div>
              <div className="drawer-content">
                <div className="drawer-section">
                  <h4>Status</h4>
                  <span className={`status-badge ${selectedAgent.status}`}>{selectedAgent.status}</span>
                </div>
                <div className="drawer-section">
                  <h4>Description</h4>
                  <p>{selectedAgent.description || 'No description available'}</p>
                </div>
                <div className="drawer-section">
                  <h4>Statistics</h4>
                  <div className="stat-row">
                    <span>Requests</span>
                    <span>{selectedAgent.requests || 0}</span>
                  </div>
                  <div className="stat-row">
                    <span>Tokens</span>
                    <span>{selectedAgent.tokens || 0}</span>
                  </div>
                  <div className="stat-row">
                    <span>Avg Response Time</span>
                    <span>{selectedAgent.avgResponseTime || '--'}ms</span>
                  </div>
                </div>
              </div>
            </aside>
            <div className="drawer-backdrop" onClick={() => setSelectedAgent(null)}></div>
          </>
        )}
      </main>

      <footer className="dashboard-footer">
        <div className="footer-info">
          <span>Progenitor</span>
          <span className="separator">•</span>
          <span>{agents.length} agents registered</span>
        </div>
        <div className="footer-status">
          <span className="pulse-dot"></span>
          <span>Last update: {new Date().toLocaleTimeString()}</span>
        </div>
      </footer>
    </div>
  );
}

export default AgentsPage;
