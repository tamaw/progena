import { useCallback, useEffect, useState } from 'react';
import Header from '../components/layout/Header';
import { useApi } from '../hooks/useApi';
import { useStatusSSE } from '../hooks/useSSE';

function formatLogTime(value) {
  if (!value) {
    return '--';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function LogsPage() {
  const { connectionState, getLogs, getWidgets } = useApi();
  const [entries, setEntries] = useState([]);
  const [widgets, setWidgets] = useState([]);
  const [selectedSource, setSelectedSource] = useState('');
  const [search, setSearch] = useState('');
  const [level, setLevel] = useState('');
  const [error, setError] = useState('');

  const levelCounts = entries.reduce((acc, entry) => {
    const entryLevel = (entry.level || 'info').toLowerCase();
    acc[entryLevel] = (acc[entryLevel] || 0) + 1;
    return acc;
  }, { debug: 0, info: 0, warn: 0, error: 0 });

  const sourceOptions = widgets
    .map((widget) => widget.name || widget.title || widget.id)
    .filter(Boolean)
    .filter((value, index, values) => values.indexOf(value) === index)
    .sort((left, right) => left.localeCompare(right));

  const loadWidgets = useCallback(async () => {
    try {
      const response = await getWidgets();
      setWidgets(response.items || response.widgets || response || []);
    } catch (loadError) {
      console.warn('[LogsPage] Failed to load widgets:', loadError);
    }
  }, [getWidgets]);

  const loadLogs = useCallback(async () => {
    try {
      setError('');
      const response = await getLogs({
        source: selectedSource || undefined,
        search: search || undefined,
        level: level || undefined,
        format: 'json',
        limit: 250,
      });
      setEntries(response.entries || []);
    } catch (loadError) {
      console.error('[LogsPage] Failed to load logs:', loadError);
      setError(loadError.message);
    }
  }, [getLogs, level, search, selectedSource]);

  useEffect(() => {
    void (async () => {
      await loadWidgets();
    })();
  }, [loadWidgets]);

  useEffect(() => {
    void (async () => {
      await loadLogs();
    })();
  }, [loadLogs]);

  const handleStatusEvent = useCallback((event) => {
    if (event?.type === 'connected' || event?.type === 'status' || event?.type === 'heartbeat') {
      loadLogs();
      loadWidgets();
    }
  }, [loadLogs, loadWidgets]);

  useStatusSSE(handleStatusEvent);

  return (
    <div className="dashboard-container">
      <Header connectionState={connectionState} />
      <main className="logs-main">
        <section className="logs-toolbar">
          <div className="toolbar-left">
            <div className="search-box">
              <span className="search-icon">🔍</span>
              <input
                type="text"
                placeholder="Search messages, sources, or paths..."
                className="search-input"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            <select
              className="filter-select"
              value={selectedSource}
              onChange={(event) => setSelectedSource(event.target.value)}
            >
              <option value="">All Sources</option>
              {sourceOptions.map((source) => (
                <option key={source} value={source}>
                  {source}
                </option>
              ))}
            </select>
          </div>
          <div className="toolbar-right">
            <div className="filter-buttons">
              {['', 'info', 'warn', 'error', 'debug'].map((value) => (
                <button
                  key={value || 'all'}
                  className={`filter-btn ${level === value ? 'active' : ''}`}
                  onClick={() => setLevel(value)}
                >
                  {value || 'All Levels'}
                </button>
              ))}
            </div>
            <button className="btn btn-secondary" onClick={() => void loadLogs()}>
              Refresh
            </button>
          </div>
        </section>

        <section className="log-stats-bar">
          <div className="log-stats">
            <span className="stat">Showing {entries.length} entries</span>
            <span className="stat">Source: {selectedSource || 'All'}</span>
            <span className="stat">Filter: {level || 'All levels'}</span>
          </div>
          <div className="level-legend">
            <span className="legend-item info">Info {levelCounts.info}</span>
            <span className="legend-item warn">Warn {levelCounts.warn}</span>
            <span className="legend-item error">Error {levelCounts.error}</span>
            <span className="legend-item debug">Debug {levelCounts.debug}</span>
          </div>
        </section>

        <section className="logs-container-full">
          <div className="detail-header">
            <h3>Runtime Logs</h3>
            <span className="badge">{entries.length}</span>
          </div>
          <div className="logs-scroll-area">
            {error ? (
              <div className="empty-state">
                <span>{error}</span>
              </div>
            ) : null}
            {!error && entries.length === 0 ? (
              <div className="empty-state">
                <span className="empty-icon">📜</span>
                <span>No logs available</span>
              </div>
            ) : null}
            {entries.length > 0 ? (
              <div className="logs-virtualized">
                {entries.map((entry, index) => (
                  <div
                    key={`${entry.path || entry.source}-${entry.timestamp || index}`}
                    className={`log-entry-full ${entry.level === 'error' ? 'error-entry' : ''}`}
                  >
                    <span className="log-timestamp">{formatLogTime(entry.time || entry.timestamp)}</span>
                    <span className={`log-level-badge ${(entry.level || 'info').toLowerCase()}`}>
                      {entry.level || 'info'}
                    </span>
                    <span className="log-message-full">{entry.message}</span>
                    <span className="log-source">{entry.source || 'system'}</span>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </section>
      </main>
    </div>
  );
}

export default LogsPage;
