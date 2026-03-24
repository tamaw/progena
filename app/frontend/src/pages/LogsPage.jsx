import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import Header from '../components/layout/Header';
import { useConfig } from '../context/ConfigContext';
import { useApi } from '../hooks/useApi';
import { useEventsSSE } from '../hooks/useSSE';

const COLLAPSED_ROW_HEIGHT = 64;
const EXPANDED_ROW_HEIGHT = 228;
const OVERSCAN_ROWS = 8;
const INCREMENTAL_LIMIT = 250;

function formatLogTime(value) {
  if (!value) {
    return '--';
  }
  const raw = String(value);
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/.test(raw)
    ? raw.replace(' ', 'T')
    : raw;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString([], {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function getEntryId(entry) {
  return entry.id || [
    entry.relative_path || entry.path || '',
    entry.timestamp || '',
    entry.level || '',
    entry.source || '',
    entry.message || '',
  ].join('|');
}

function buildQueryParams(filters, extra = {}) {
  const params = {};
  if (filters.folder) {
    params.folder = filters.folder;
  }
  if (filters.filePath) {
    params.file = filters.filePath;
  }
  if (filters.search) {
    params.search = filters.search;
  }
  if (filters.level) {
    params.level = filters.level;
  }
  if (filters.searchMode && filters.searchMode !== 'plain') {
    params.search_mode = filters.searchMode;
  }
  return { ...params, ...extra };
}

function mergeEntries(currentEntries, incomingEntries) {
  if (!incomingEntries.length) {
    return currentEntries;
  }
  const seen = new Set(currentEntries.map((entry) => getEntryId(entry)));
  const freshEntries = incomingEntries.filter((entry) => !seen.has(getEntryId(entry)));
  return freshEntries.length > 0 ? [...freshEntries, ...currentEntries] : currentEntries;
}

function findVisibleRange(items, scrollTop, viewportHeight) {
  if (!items.length) {
    return [0, -1];
  }

  const overscan = OVERSCAN_ROWS * COLLAPSED_ROW_HEIGHT;
  const startBoundary = Math.max(0, scrollTop - overscan);
  const endBoundary = scrollTop + viewportHeight + overscan;

  let startIndex = 0;
  while (startIndex < items.length && (items[startIndex].top + items[startIndex].height) < startBoundary) {
    startIndex += 1;
  }

  let endIndex = startIndex;
  while (endIndex < items.length && items[endIndex].top < endBoundary) {
    endIndex += 1;
  }

  return [startIndex, Math.max(startIndex, endIndex - 1)];
}

function LogsPage() {
  const { connectionState, getLogs } = useApi();
  const { getApiUrl } = useConfig();
  const [searchParams, setSearchParams] = useSearchParams();
  const [entries, setEntries] = useState([]);
  const [sourceGroups, setSourceGroups] = useState([]);
  const [selectedFolder, setSelectedFolder] = useState(searchParams.get('folder') || '');
  const [selectedFile, setSelectedFile] = useState(searchParams.get('file') || '');
  const [search, setSearch] = useState(searchParams.get('search') || '');
  const [level, setLevel] = useState(searchParams.get('level') || '');
  const [searchMode, setSearchMode] = useState(searchParams.get('mode') === 'regex' ? 'regex' : 'plain');
  const [tailPinned, setTailPinned] = useState(searchParams.get('follow') !== '0');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [expandedIds, setExpandedIds] = useState(() => new Set());
  const [newestCursor, setNewestCursor] = useState('');
  const [hiddenNewCount, setHiddenNewCount] = useState(0);
  const [exportFormat, setExportFormat] = useState('jsonl');
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(640);
  const scrollAreaRef = useRef(null);
  const pendingScrollAdjustmentRef = useRef(null);
  const refreshTimeoutRef = useRef(null);
  const requestVersionRef = useRef(0);
  const newestCursorRef = useRef('');
  const tailPinnedRef = useRef(tailPinned);

  useEffect(() => {
    newestCursorRef.current = newestCursor;
  }, [newestCursor]);

  useEffect(() => {
    tailPinnedRef.current = tailPinned;
  }, [tailPinned]);

  useEffect(() => {
    const requestedFolder = searchParams.get('folder') || '';
    const requestedFile = searchParams.get('file') || '';
    const requestedSearch = searchParams.get('search') || '';
    const requestedLevel = searchParams.get('level') || '';
    const requestedMode = searchParams.get('mode') === 'regex' ? 'regex' : 'plain';
    const requestedFollow = searchParams.get('follow') !== '0';

    setSelectedFolder((current) => (current === requestedFolder ? current : requestedFolder));
    setSelectedFile((current) => (current === requestedFile ? current : requestedFile));
    setSearch((current) => (current === requestedSearch ? current : requestedSearch));
    setLevel((current) => (current === requestedLevel ? current : requestedLevel));
    setSearchMode((current) => (current === requestedMode ? current : requestedMode));
    setTailPinned((current) => (current === requestedFollow ? current : requestedFollow));
  }, [searchParams]);

  useEffect(() => {
    const nextParams = new URLSearchParams();
    if (selectedFolder) {
      nextParams.set('folder', selectedFolder);
    }
    if (selectedFile) {
      nextParams.set('file', selectedFile);
    }
    if (search) {
      nextParams.set('search', search);
    }
    if (level) {
      nextParams.set('level', level);
    }
    if (searchMode !== 'plain') {
      nextParams.set('mode', searchMode);
    }
    if (!tailPinned) {
      nextParams.set('follow', '0');
    }

    if (nextParams.toString() !== searchParams.toString()) {
      setSearchParams(nextParams, { replace: true });
    }
  }, [level, search, searchMode, searchParams, selectedFile, selectedFolder, setSearchParams, tailPinned]);

  const filters = useMemo(() => ({
    folder: selectedFolder,
    filePath: selectedFile,
    search,
    level,
    searchMode,
  }), [level, search, searchMode, selectedFile, selectedFolder]);

  const availableFiles = useMemo(() => {
    if (selectedFolder) {
      return sourceGroups.find((group) => group.id === selectedFolder)?.options || [];
    }
    return sourceGroups.flatMap((group) => group.options || []).sort((left, right) => (
      (left.path || left.id).localeCompare(right.path || right.id)
    ));
  }, [selectedFolder, sourceGroups]);

  useEffect(() => {
    if (selectedFolder && !sourceGroups.some((group) => group.id === selectedFolder)) {
      setSelectedFolder('');
    }
  }, [selectedFolder, sourceGroups]);

  useEffect(() => {
    if (selectedFile && !availableFiles.some((file) => file.id === selectedFile)) {
      setSelectedFile('');
    }
  }, [availableFiles, selectedFile]);

  const rowLayout = useMemo(() => {
    let totalHeight = 0;
    const items = entries.map((entry) => {
      const id = getEntryId(entry);
      const expanded = expandedIds.has(id);
      const height = expanded ? EXPANDED_ROW_HEIGHT : COLLAPSED_ROW_HEIGHT;
      const row = { entry, id, top: totalHeight, height, expanded };
      totalHeight += height;
      return row;
    });
    return { items, totalHeight };
  }, [entries, expandedIds]);

  const levelCounts = useMemo(() => entries.reduce((acc, entry) => {
    const entryLevel = (entry.level || 'info').toLowerCase();
    acc[entryLevel] = (acc[entryLevel] || 0) + 1;
    return acc;
  }, { debug: 0, info: 0, warn: 0, error: 0 }), [entries]);

  const visibleRows = useMemo(() => {
    const [startIndex, endIndex] = findVisibleRange(rowLayout.items, scrollTop, viewportHeight);
    return rowLayout.items.slice(startIndex, endIndex + 1);
  }, [rowLayout.items, scrollTop, viewportHeight]);

  const syncViewportMetrics = useCallback(() => {
    const scrollArea = scrollAreaRef.current;
    if (!scrollArea) {
      return;
    }
    setViewportHeight(scrollArea.clientHeight || 640);
    setScrollTop(scrollArea.scrollTop || 0);
  }, []);

  useEffect(() => {
    syncViewportMetrics();
    window.addEventListener('resize', syncViewportMetrics);
    return () => window.removeEventListener('resize', syncViewportMetrics);
  }, [syncViewportMetrics]);

  useLayoutEffect(() => {
    const scrollArea = scrollAreaRef.current;
    const adjustment = pendingScrollAdjustmentRef.current;
    if (!scrollArea || !adjustment) {
      return;
    }

    if (adjustment.mode === 'top') {
      scrollArea.scrollTop = 0;
      setScrollTop(0);
    } else if (adjustment.mode === 'offset') {
      const nextScrollTop = scrollArea.scrollTop + adjustment.delta;
      scrollArea.scrollTop = nextScrollTop;
      setScrollTop(nextScrollTop);
    }

    pendingScrollAdjustmentRef.current = null;
  }, [entries]);

  const loadLogs = useCallback(async ({ incremental = false } = {}) => {
    const requestVersion = requestVersionRef.current + 1;
    requestVersionRef.current = requestVersion;

    if (!incremental) {
      setLoading(true);
      pendingScrollAdjustmentRef.current = { mode: 'top' };
      setHiddenNewCount(0);
    }

    try {
      setError('');
      const response = await getLogs(buildQueryParams(filters, {
        format: 'json',
        after: incremental ? newestCursorRef.current || undefined : undefined,
        limit: incremental ? INCREMENTAL_LIMIT : 0,
      }));

      if (requestVersion !== requestVersionRef.current) {
        return;
      }

      setSourceGroups(response.source_groups || []);

      if (incremental) {
        if (!response.cursor_found) {
          void loadLogs({ incremental: false });
          return;
        }

        const incomingEntries = response.entries || [];
        if (incomingEntries.length === 0) {
          if (response.newest_cursor) {
            setNewestCursor(response.newest_cursor);
          }
          return;
        }

        const shouldStickToTop = tailPinnedRef.current || (scrollAreaRef.current?.scrollTop || 0) <= 4;
        if (shouldStickToTop) {
          pendingScrollAdjustmentRef.current = { mode: 'top' };
          setHiddenNewCount(0);
          setTailPinned(true);
        } else {
          pendingScrollAdjustmentRef.current = {
            mode: 'offset',
            delta: incomingEntries.length * COLLAPSED_ROW_HEIGHT,
          };
          setHiddenNewCount((current) => current + incomingEntries.length);
        }

        setEntries((currentEntries) => mergeEntries(currentEntries, incomingEntries));
        setNewestCursor(response.newest_cursor || newestCursorRef.current);
        return;
      }

      setEntries(response.entries || []);
      setNewestCursor(response.newest_cursor || '');
      setExpandedIds(new Set());
    } catch (loadError) {
      console.error('[LogsPage] Failed to load logs:', loadError);
      setError(loadError.message);
    } finally {
      if (!incremental && requestVersion === requestVersionRef.current) {
        setLoading(false);
      }
    }
  }, [filters, getLogs]);

  useEffect(() => {
    void loadLogs({ incremental: false });
  }, [loadLogs]);

  useEffect(() => {
    return () => {
      if (refreshTimeoutRef.current) {
        window.clearTimeout(refreshTimeoutRef.current);
      }
    };
  }, []);

  const queueIncrementalRefresh = useCallback(() => {
    if (refreshTimeoutRef.current) {
      return;
    }

    refreshTimeoutRef.current = window.setTimeout(() => {
      refreshTimeoutRef.current = null;
      void loadLogs({ incremental: true });
    }, 250);
  }, [loadLogs]);

  const handleEvent = useCallback((event) => {
    if (event?.type === 'connected') {
      if (newestCursorRef.current) {
        queueIncrementalRefresh();
      } else {
        void loadLogs({ incremental: false });
      }
      return;
    }

    if (['agent', 'agent_message', 'scheduled_response', 'task', 'alert', 'shutdown', 'message'].includes(event?.type)) {
      queueIncrementalRefresh();
    }
  }, [loadLogs, queueIncrementalRefresh]);

  useEventsSSE(handleEvent);

  const handleScroll = useCallback((event) => {
    const nextScrollTop = event.currentTarget.scrollTop;
    setScrollTop(nextScrollTop);
    setViewportHeight(event.currentTarget.clientHeight || 640);

    if (nextScrollTop <= 4) {
      if (!tailPinnedRef.current) {
        setTailPinned(true);
      }
      if (hiddenNewCount > 0) {
        setHiddenNewCount(0);
      }
    } else if (tailPinnedRef.current) {
      setTailPinned(false);
    }
  }, [hiddenNewCount]);

  const jumpToNewest = useCallback(() => {
    pendingScrollAdjustmentRef.current = { mode: 'top' };
    setHiddenNewCount(0);
    setTailPinned(true);
    requestAnimationFrame(() => {
      if (scrollAreaRef.current) {
        scrollAreaRef.current.scrollTop = 0;
        setScrollTop(0);
      }
    });
  }, []);

  const toggleExpanded = useCallback((entryId) => {
    setExpandedIds((current) => {
      const next = new Set(current);
      if (next.has(entryId)) {
        next.delete(entryId);
      } else {
        next.add(entryId);
      }
      return next;
    });
  }, []);

  const handleExport = useCallback(() => {
    const exportUrl = new URL(getApiUrl('/api/ui/logs/export'), window.location.origin);
    const params = buildQueryParams(filters, { format: exportFormat });
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        exportUrl.searchParams.set(key, String(value));
      }
    });
    window.open(exportUrl.toString(), '_blank', 'noopener,noreferrer');
  }, [exportFormat, filters, getApiUrl]);

  const selectedFolderLabel = selectedFolder || 'All folders';
  const selectedFileLabel = selectedFile || 'All files';

  return (
    <div className="dashboard-container">
      <Header connectionState={connectionState} />
      <main className="logs-main">
        <section className="logs-toolbar">
          <div className="toolbar-left logs-toolbar-grid">
            <div className="search-box">
              <span className="search-icon">🔍</span>
              <input
                type="text"
                placeholder={searchMode === 'regex' ? 'Regex over message, source, and path...' : 'Search logs...'}
                className="search-input"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            <select
              className="filter-select"
              value={searchMode}
              onChange={(event) => setSearchMode(event.target.value)}
            >
              <option value="plain">Plain Search</option>
              <option value="regex">Regex Search</option>
            </select>
            <select
              className="filter-select"
              value={selectedFolder}
              onChange={(event) => {
                setSelectedFolder(event.target.value);
                setSelectedFile('');
              }}
            >
              <option value="">All Folders</option>
              {sourceGroups.map((group) => (
                <option key={group.id} value={group.id}>
                  {group.label} ({(group.options || []).length})
                </option>
              ))}
            </select>
            <select
              className="filter-select"
              value={selectedFile}
              onChange={(event) => setSelectedFile(event.target.value)}
            >
              <option value="">{selectedFolder ? 'All Files In Folder' : 'All Files'}</option>
              {availableFiles.map((file) => (
                <option key={file.id} value={file.id}>
                  {selectedFolder ? file.label : (file.path || file.id)}
                </option>
              ))}
            </select>
          </div>
          <div className="toolbar-right logs-toolbar-grid">
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
            <button
              className={`btn btn-secondary ${tailPinned ? 'active-toggle' : ''}`}
              onClick={() => {
                const nextPinned = !tailPinned;
                setTailPinned(nextPinned);
                if (nextPinned) {
                  jumpToNewest();
                }
              }}
            >
              {tailPinned ? 'Following Newest' : 'Resume Follow'}
            </button>
            <select
              className="filter-select export-select"
              value={exportFormat}
              onChange={(event) => setExportFormat(event.target.value)}
            >
              <option value="jsonl">Export JSONL</option>
              <option value="json">Export JSON</option>
              <option value="text">Export Text</option>
            </select>
            <button className="btn btn-secondary" onClick={handleExport}>
              Download
            </button>
            <button className="btn btn-secondary" onClick={() => void loadLogs({ incremental: false })}>
              Refresh
            </button>
          </div>
        </section>

        <section className="log-stats-bar">
          <div className="log-stats">
            <span className="stat">Showing {entries.length} entries</span>
            <span className="stat">Folder: {selectedFolderLabel}</span>
            <span className="stat">File: {selectedFileLabel}</span>
            <span className="stat">Mode: {searchMode}</span>
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
            <div className="logs-header-copy">
              <h3>Runtime Logs</h3>
              <span className="badge">{entries.length}</span>
            </div>
            <div className="logs-header-actions">
              {!tailPinned && hiddenNewCount > 0 ? (
                <button className="btn btn-primary new-logs-pill" onClick={jumpToNewest}>
                  {hiddenNewCount} new log{hiddenNewCount === 1 ? '' : 's'}
                </button>
              ) : null}
              <span className={`tail-status ${tailPinned ? 'following' : 'paused'}`}>
                {tailPinned ? 'Following newest' : 'Follow paused'}
              </span>
            </div>
          </div>
          <div ref={scrollAreaRef} className="logs-scroll-area" onScroll={handleScroll}>
            {error ? (
              <div className="empty-state">
                <span>{error}</span>
              </div>
            ) : null}
            {!error && loading ? (
              <div className="empty-state">
                <span>Loading logs…</span>
              </div>
            ) : null}
            {!error && !loading && entries.length === 0 ? (
              <div className="empty-state">
                <span className="empty-icon">📜</span>
                <span>No logs available</span>
              </div>
            ) : null}
            {!error && entries.length > 0 ? (
              <div className="logs-virtualized" style={{ height: `${rowLayout.totalHeight}px` }}>
                {visibleRows.map((row) => (
                  <div
                    key={row.id}
                    className={`log-entry-full ${row.entry.level === 'error' ? 'error-entry' : ''} ${row.expanded ? 'expanded' : ''}`}
                    style={{ top: `${row.top}px`, height: `${row.height}px` }}
                  >
                    <div className="log-row-header">
                      <span className="log-timestamp">{formatLogTime(row.entry.time || row.entry.timestamp)}</span>
                      <span className={`log-level-badge ${(row.entry.level || 'info').toLowerCase()}`}>
                        {row.entry.level || 'info'}
                      </span>
                      <span className="log-message-full">{row.entry.message}</span>
                      <span className="log-source">{row.entry.relative_path || row.entry.source || 'system'}</span>
                      {row.entry.data ? (
                        <button
                          className="log-expand-btn"
                          onClick={() => toggleExpanded(row.id)}
                          type="button"
                        >
                          {row.expanded ? 'Collapse' : 'Expand JSON'}
                        </button>
                      ) : (
                        <span className="log-expand-spacer" />
                      )}
                    </div>
                    {row.expanded && row.entry.data ? (
                      <div className="log-data-preview">
                        <pre>{JSON.stringify(row.entry.data, null, 2)}</pre>
                      </div>
                    ) : null}
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
