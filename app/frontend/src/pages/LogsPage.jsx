import { useState, useEffect, useCallback, useRef } from 'react';
import { useApi } from '../hooks/useApi';
import { useEventsSSE } from '../hooks/useSSE';
import Header from '../components/layout/Header';

function LogsPage() {
  const { connectionState } = useApi();
  const [logs, setLogs] = useState([]);
  const [filter, setFilter] = useState({ level: 'all', source: 'all', search: '' });
  const [isPaused, setIsPaused] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const [selectedLog, setSelectedLog] = useState(null);
  const logsContainerRef = useRef(null);

  const handleChatEvent = useCallback((event) => {
    if (event.type === 'log' && !isPaused) {
      setLogs(prev => [...prev.slice(-999), {
        id: Date.now(),
        timestamp: new Date().toISOString(),
        ...event.data
      }]);
    }
  }, [isPaused]);

  // Use chat SSE stream for log events
  useEventsSSE(handleChatEvent);

  useEffect(() => {
    if (autoScroll && logsContainerRef.current) {
      logsContainerRef.current.scrollTop = logsContainerRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  const filteredLogs = logs.filter(log => {
    const matchesLevel = filter.level === 'all' || log.level === filter.level;
    const matchesSource = filter.source === 'all' || log.source === filter.source;
    const matchesSearch = !filter.search ||
      log.message?.toLowerCase().includes(filter.search.toLowerCase());
    return matchesLevel && matchesSource && matchesSearch;
  });

  const stats = {
    total: logs.length,
    error: logs.filter(l => l.level === 'error').length,
    warn: logs.filter(l => l.level === 'warn').length,
    info: logs.filter(l => l.level === 'info').length,
    debug: logs.filter(l => l.level === 'debug').length
  };

  const clearLogs = () => setLogs([]);

  const exportLogs = () => {
    const data = JSON.stringify(logs, null, 2);
    const blob = new Blob([data], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `progenitor-logs-${new Date().toISOString()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const getLevelIcon = (level) => {
    switch (level) {
      case 'error': return '❌';
      case 'warn': return '⚠️';
      case 'info': return 'ℹ️';
      case 'debug': return '🔍';
      default: return '📝';
    }
  };

  return (
    <div className="dashboard-container">
      <Header connectionState={connectionState} />
      <main className="logs-main">
        {/* Toolbar */}
        <section className="logs-toolbar">
          <div className="toolbar-left">
            <div className="search-box">
              <span className="search-icon">🔍</span>
              <input
                type="text"
                placeholder="Search logs..."
                className="search-input"
                value={filter.search}
                onChange={(e) => setFilter(prev => ({ ...prev, search: e.target.value }))}
              />
            </div>
            <select
              className="filter-select"
              value={filter.level}
              onChange={(e) => setFilter(prev => ({ ...prev, level: e.target.value }))}
            >
              <option value="all">All Levels</option>
              <option value="debug">Debug</option>
              <option value="info">Info</option>
              <option value="warn">Warning</option>
              <option value="error">Error</option>
            </select>
            <select
              className="filter-select"
              value={filter.source}
              onChange={(e) => setFilter(prev => ({ ...prev, source: e.target.value }))}
            >
              <option value="all">All Sources</option>
              <option value="progenitor">Progenitor</option>
              <option value="planner">Planner</option>
              <option value="executor">Executor</option>
              <option value="backend">Backend</option>
              <option value="llm">LLM Client</option>
            </select>
          </div>
          <div className="toolbar-right">
            <div className="log-stats">
              <span className="stat">{filteredLogs.length} entries</span>
              <span className="stat error">{stats.error} errors</span>
              <span className="stat warning">{stats.warn} warnings</span>
            </div>
            <div className="toolbar-actions">
              <button
                className={`btn-mini ${autoScroll ? 'active' : ''}`}
                onClick={() => setAutoScroll(!autoScroll)}
                title="Auto-scroll"
              >⬇️</button>
              <button
                className={`btn-mini ${isPaused ? 'active' : ''}`}
                onClick={() => setIsPaused(!isPaused)}
                title={isPaused ? 'Resume' : 'Pause'}
              >{isPaused ? '▶️' : '⏸️'}</button>
              <button className="btn-mini" onClick={clearLogs} title="Clear">🗑️</button>
              <button className="btn-mini" onClick={exportLogs} title="Export">📥</button>
            </div>
          </div>
        </section>

        {/* Level Stats Bar */}
        <section className="log-stats-bar">
          <div className="log-level-breakdown">
            <div className="level-bar debug" style={{ width: `${(stats.debug / Math.max(stats.total, 1)) * 100}%` }}></div>
            <div className="level-bar info" style={{ width: `${(stats.info / Math.max(stats.total, 1)) * 100}%` }}></div>
            <div className="level-bar warn" style={{ width: `${(stats.warn / Math.max(stats.total, 1)) * 100}%` }}></div>
            <div className="level-bar error" style={{ width: `${(stats.error / Math.max(stats.total, 1)) * 100}%` }}></div>
          </div>
          <div className="level-legend">
            <span className="legend-item debug">Debug</span>
            <span className="legend-item info">Info</span>
            <span className="legend-item warn">Warning</span>
            <span className="legend-item error">Error</span>
          </div>
        </section>

        {/* Logs Container */}
        <section className="logs-container-full" ref={logsContainerRef}>
          {filteredLogs.length > 0 ? (
            filteredLogs.map(log => (
              <div
                key={log.id}
                className={`log-entry ${log.level}`}
                onClick={() => setSelectedLog(log)}
              >
                <span className="log-timestamp">
                  {new Date(log.timestamp).toLocaleTimeString()}
                </span>
                <span className="log-level">{getLevelIcon(log.level)}</span>
                <span className="log-source">[{log.source || 'system'}]</span>
                <span className="log-message">{log.message}</span>
              </div>
            ))
          ) : (
            <div className="empty-state">
              <span className="empty-icon">📜</span>
              <span>Waiting for logs...</span>
              <span className="empty-hint">Logs will appear here in real-time</span>
            </div>
          )}
        </section>

        {/* Log Detail */}
        {selectedLog && (
          <section className="log-detail">
            <div className="detail-header">
              <h3>Log Details</h3>
              <button className="btn-mini" onClick={() => setSelectedLog(null)}>×</button>
            </div>
            <div className="detail-content">
              <div className="detail-row">
                <span className="detail-label">Timestamp</span>
                <span className="detail-value">{selectedLog.timestamp}</span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Level</span>
                <span className={`detail-value level-${selectedLog.level}`}>
                  {getLevelIcon(selectedLog.level)} {selectedLog.level}
                </span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Source</span>
                <span className="detail-value">{selectedLog.source || 'system'}</span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Message</span>
                <span className="detail-value">{selectedLog.message}</span>
              </div>
              {selectedLog.data && (
                <div className="detail-row">
                  <span className="detail-label">Data</span>
                  <pre className="detail-data">{JSON.stringify(selectedLog.data, null, 2)}</pre>
                </div>
              )}
            </div>
          </section>
        )}
      </main>

      <footer className="dashboard-footer">
        <div className="footer-info">
          <span>Progenitor</span>
          <span className="separator">•</span>
          <span>{isPaused ? 'Paused' : 'Live'}</span>
        </div>
        <div className="footer-status">
          <span className={`pulse-dot ${isPaused ? 'paused' : ''}`}></span>
          <span>Last update: {new Date().toLocaleTimeString()}</span>
        </div>
      </footer>
    </div>
  );
}

export default LogsPage;
