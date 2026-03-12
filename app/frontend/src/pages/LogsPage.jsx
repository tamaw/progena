import { useCallback, useEffect, useState } from 'react';
import Header from '../components/layout/Header';
import { useApi } from '../hooks/useApi';
import { useStatusSSE } from '../hooks/useSSE';

function LogsPage() {
  const { connectionState, getLogs, getWidgets } = useApi();
  const [entries, setEntries] = useState([]);
  const [widgets, setWidgets] = useState([]);
  const [selectedSource, setSelectedSource] = useState('');
  const [search, setSearch] = useState('');
  const [level, setLevel] = useState('');
  const [error, setError] = useState('');

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
      <main className="agents-main">
        <section className="agents-toolbar">
          <div className="search-box">
            <span className="search-icon">🔍</span>
            <input
              type="text"
              placeholder="Search logs..."
              className="search-input"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
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
          <select
            className="search-input"
            value={selectedSource}
            onChange={(event) => setSelectedSource(event.target.value)}
          >
            <option value="">All Sources</option>
            {widgets.map((widget) => (
              <option key={widget.id} value={widget.name || widget.title || widget.id}>
                {widget.title || widget.name || widget.id}
              </option>
            ))}
          </select>
        </section>

        <section className="dashboard-panel activity-panel">
          <div className="panel-header">
            <h3>Runtime Logs</h3>
            <span className="badge">{entries.length}</span>
          </div>
          <div className="panel-body">
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
              <div className="timeline">
                {entries.map((entry, index) => (
                  <div key={`${entry.path || entry.source}-${entry.timestamp || index}`} className="timeline-item">
                    <strong>[{entry.level || 'info'}] {entry.source || 'system'}</strong>
                    <span>{entry.message}</span>
                    <span>{entry.time || entry.timestamp || '--'}</span>
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
