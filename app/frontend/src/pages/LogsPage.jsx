import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import Header from '../components/layout/Header';
import { useConfig } from '../context/ConfigContext';
import { useApi } from '../hooks/useApi';
import { useEventsSSE } from '../hooks/useSSE';

const COLLAPSED_ROW_HEIGHT = 38;
const EXPANDED_ROW_HEIGHT = 180;
const OVERSCAN_ROWS = 8;
const INCREMENTAL_LIMIT = 250;
const WEEKDAY_LABELS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];

function toDateInputValue(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function defaultDateRange() {
  const end = new Date();
  const start = new Date(end);
  start.setDate(end.getDate() - 6);
  return {
    start: toDateInputValue(start),
    end: toDateInputValue(end),
  };
}

function parseDateInput(value) {
  if (!value) {
    return null;
  }
  const [year, month, day] = value.split('-').map(Number);
  if (!year || !month || !day) {
    return null;
  }
  return new Date(year, month - 1, day);
}

function monthStart(date) {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

function addMonths(date, amount) {
  return new Date(date.getFullYear(), date.getMonth() + amount, 1);
}

function sameDay(left, right) {
  return Boolean(left && right)
    && left.getFullYear() === right.getFullYear()
    && left.getMonth() === right.getMonth()
    && left.getDate() === right.getDate();
}

function dayInRange(day, start, end) {
  if (!start || !end) {
    return false;
  }
  return day >= start && day <= end;
}

function buildMonthGrid(monthDate) {
  const firstDay = monthStart(monthDate);
  const firstWeekday = firstDay.getDay();
  const gridStart = new Date(firstDay);
  gridStart.setDate(firstDay.getDate() - firstWeekday);

  return Array.from({ length: 42 }, (_, index) => {
    const value = new Date(gridStart);
    value.setDate(gridStart.getDate() + index);
    return value;
  });
}

function formatLogTime(value) {
  if (!value) {
    return '--';
  }
  const raw = String(value);
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/.test(raw) ? raw.replace(' ', 'T') : raw;
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

function normalizeLevel(value) {
  const level = String(value || 'info').toLowerCase();
  if (level === 'warning') {
    return 'warn';
  }
  if (level === 'fatal') {
    return 'error';
  }
  return level;
}

function buildQueryParams({ group, filePath, search, startDate, endDate }, extra = {}) {
  const params = {};
  if (group) {
    params.folder = group;
  }
  if (filePath) {
    params.file = filePath;
  }
  if (search) {
    params.search = search;
  }
  if (startDate) {
    params.start = startDate;
  }
  if (endDate) {
    params.end = endDate;
  }
  return { ...params, ...extra };
}

function formatGroupLabel(groupId) {
  return groupId === 'root' ? 'Root' : groupId;
}

function formatFileLabel(groupId, file) {
  const path = file.path || file.id || '';
  if (!groupId || groupId === 'root') {
    return path;
  }
  const prefix = `${groupId}/`;
  return path.startsWith(prefix) ? path.slice(prefix.length) : path;
}

function mergeEntries(currentEntries, incomingEntries) {
  if (!incomingEntries.length) {
    return currentEntries;
  }
  const seen = new Set(currentEntries.map((entry) => getEntryId(entry)));
  const freshEntries = incomingEntries.filter((entry) => !seen.has(getEntryId(entry)));
  return freshEntries.length > 0 ? [...freshEntries, ...currentEntries] : currentEntries;
}

function hasActiveFilters({ group, filePath, search, level }) {
  return Boolean(group || filePath || search || level);
}

function getDefaultSelection(groups) {
  for (const group of groups || []) {
    const firstFile = (group.options || [])[0];
    if (firstFile) {
      return { group: group.id || '', file: firstFile.id || '' };
    }
  }
  return { group: '', file: '' };
}

function findVisibleRange(items, scrollTop, viewportHeight) {
  if (!items.length) {
    return [0, -1];
  }

  const overscan = OVERSCAN_ROWS * COLLAPSED_ROW_HEIGHT;
  const startBoundary = Math.max(0, scrollTop - overscan);
  const endBoundary = scrollTop + viewportHeight + overscan;

  let startIndex = 0;
  while (startIndex < items.length && items[startIndex].top + items[startIndex].height < startBoundary) {
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
  const defaultRange = useMemo(() => defaultDateRange(), []);
  const [entries, setEntries] = useState([]);
  const [sourceGroups, setSourceGroups] = useState([]);
  const [selectedGroup, setSelectedGroup] = useState(searchParams.get('group') || '');
  const [selectedFile, setSelectedFile] = useState(searchParams.get('file') || '');
  const [search, setSearch] = useState(searchParams.get('search') || '');
  const [level, setLevel] = useState(searchParams.get('level') || '');
  const [startDate, setStartDate] = useState(searchParams.get('start') || '');
  const [endDate, setEndDate] = useState(searchParams.get('end') || '');
  const [loadingEntries, setLoadingEntries] = useState(true);
  const [error, setError] = useState('');
  const [expandedIds, setExpandedIds] = useState(() => new Set());
  const [newestCursor, setNewestCursor] = useState('');
  const [hiddenNewCount, setHiddenNewCount] = useState(0);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(640);
  const [tailPinned, setTailPinned] = useState(true);
  const scrollAreaRef = useRef(null);
  const pendingScrollAdjustmentRef = useRef(null);
  const refreshTimeoutRef = useRef(null);
  const requestVersionRef = useRef(0);
  const newestCursorRef = useRef('');
  const tailPinnedRef = useRef(true);
  const defaultSelectionAppliedRef = useRef(false);
  const datePickerRef = useRef(null);
  const [datePickerOpen, setDatePickerOpen] = useState(false);
  const [calendarMonth, setCalendarMonth] = useState(() => {
    const start = parseDateInput(searchParams.get('start') || defaultRange.start) || new Date();
    return monthStart(start);
  });
  const [draftRange, setDraftRange] = useState(() => ({
    start: searchParams.get('start') || defaultRange.start,
    end: searchParams.get('end') || defaultRange.end,
  }));
  const [rangeAnchor, setRangeAnchor] = useState(null);
  const loading = loadingEntries;

  useEffect(() => {
    newestCursorRef.current = newestCursor;
  }, [newestCursor]);

  useEffect(() => {
    tailPinnedRef.current = tailPinned;
  }, [tailPinned]);

  useEffect(() => {
    const requestedGroup = searchParams.get('group') || '';
    const requestedFile = searchParams.get('file') || '';
    const requestedSearch = searchParams.get('search') || '';
    const requestedLevel = searchParams.get('level') || '';
    const requestedStart = searchParams.get('start') || '';
    const requestedEnd = searchParams.get('end') || '';

    setSelectedGroup((current) => (current === requestedGroup ? current : requestedGroup));
    setSelectedFile((current) => (current === requestedFile ? current : requestedFile));
    setSearch((current) => (current === requestedSearch ? current : requestedSearch));
    setLevel((current) => (current === requestedLevel ? current : requestedLevel));
    setStartDate((current) => (current === requestedStart ? current : requestedStart));
    setEndDate((current) => (current === requestedEnd ? current : requestedEnd));
  }, [searchParams]);

  useEffect(() => {
    const nextParams = new URLSearchParams();
    if (selectedGroup) {
      nextParams.set('group', selectedGroup);
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
    if (startDate) {
      nextParams.set('start', startDate);
    }
    if (endDate) {
      nextParams.set('end', endDate);
    }
    if (nextParams.toString() !== searchParams.toString()) {
      setSearchParams(nextParams, { replace: true });
    }
  }, [endDate, level, search, searchParams, selectedFile, selectedGroup, setSearchParams, startDate]);

  const requestFilters = useMemo(() => ({
    group: selectedGroup,
    filePath: selectedFile,
    search,
    startDate,
    endDate,
  }), [endDate, search, selectedFile, selectedGroup, startDate]);

  const availableFiles = useMemo(() => {
    if (!selectedGroup) {
      return sourceGroups.flatMap((group) => group.options || []);
    }
    return sourceGroups.find((group) => group.id === selectedGroup)?.options || [];
  }, [selectedGroup, sourceGroups]);

  useEffect(() => {
    if (selectedGroup && !sourceGroups.some((group) => group.id === selectedGroup)) {
      setSelectedGroup('');
    }
  }, [selectedGroup, sourceGroups]);

  useEffect(() => {
    if (selectedFile && !availableFiles.some((file) => file.id === selectedFile)) {
      setSelectedFile('');
    }
  }, [availableFiles, selectedFile]);

  useEffect(() => {
    const nextStart = startDate || defaultRange.start;
    const nextEnd = endDate || defaultRange.end;
    setDraftRange((current) => (
      current.start === nextStart && current.end === nextEnd
        ? current
        : { start: nextStart, end: nextEnd }
    ));
  }, [defaultRange.end, defaultRange.start, endDate, startDate]);

  useEffect(() => {
    if (!datePickerOpen) {
      setRangeAnchor(null);
      return;
    }

    const handleClickOutside = (event) => {
      if (datePickerRef.current && !datePickerRef.current.contains(event.target)) {
        setDatePickerOpen(false);
        setRangeAnchor(null);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [datePickerOpen]);

  const filteredEntries = useMemo(() => {
    if (!level) {
      return entries;
    }
    return entries.filter((entry) => normalizeLevel(entry.level) === level);
  }, [entries, level]);

  const rowLayout = useMemo(() => {
    let totalHeight = 0;
    const items = filteredEntries.map((entry) => {
      const id = getEntryId(entry);
      const expanded = expandedIds.has(id);
      const height = expanded ? EXPANDED_ROW_HEIGHT : COLLAPSED_ROW_HEIGHT;
      const row = { entry, id, top: totalHeight, height, expanded };
      totalHeight += height;
      return row;
    });
    return { items, totalHeight };
  }, [expandedIds, filteredEntries]);

  const levelCounts = useMemo(() => entries.reduce((acc, entry) => {
    const entryLevel = normalizeLevel(entry.level);
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
      setLoadingEntries(true);
      pendingScrollAdjustmentRef.current = { mode: 'top' };
      setHiddenNewCount(0);
    }

    try {
      setError('');
      const response = await getLogs(buildQueryParams(requestFilters, {
        format: 'json',
        after: incremental ? newestCursorRef.current || undefined : undefined,
        limit: incremental ? INCREMENTAL_LIMIT : 0,
      }));

      if (requestVersion !== requestVersionRef.current) {
        return;
      }

      setSourceGroups(response.source_groups || []);
      if (!incremental && response.date_range) {
        setStartDate((current) => current || response.date_range.start || defaultRange.start);
        setEndDate((current) => current || response.date_range.end || defaultRange.end);
      }

      if (!incremental && !defaultSelectionAppliedRef.current && !selectedGroup && !selectedFile) {
        const activeSelection = response.active_selection || {};
        if (activeSelection.group) {
          setSelectedGroup(activeSelection.group);
        }
        if (activeSelection.file) {
          setSelectedFile(activeSelection.file);
        }
        defaultSelectionAppliedRef.current = true;
      }

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

        if (tailPinnedRef.current || (scrollAreaRef.current?.scrollTop || 0) <= 4) {
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
        setLoadingEntries(false);
      }
    }
  }, [defaultRange.end, defaultRange.start, getLogs, requestFilters, selectedFile, selectedGroup]);

  useEffect(() => {
    void loadLogs({ incremental: false });
  }, [loadLogs]);

  useEffect(() => () => {
    if (refreshTimeoutRef.current) {
      window.clearTimeout(refreshTimeoutRef.current);
    }
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
      setTailPinned(true);
      if (hiddenNewCount > 0) {
        setHiddenNewCount(0);
      }
    } else {
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
    const params = buildQueryParams(requestFilters, { format: 'csv', ...(level ? { level } : {}) });
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        exportUrl.searchParams.set(key, String(value));
      }
    });
    window.open(exportUrl.toString(), '_blank', 'noopener,noreferrer');
  }, [getApiUrl, level, requestFilters]);

  const activeStartDate = parseDateInput(draftRange.start || startDate || defaultRange.start);
  const activeEndDate = parseDateInput(draftRange.end || endDate || defaultRange.end);
  const leftMonthDays = useMemo(() => buildMonthGrid(calendarMonth), [calendarMonth]);
  const rightMonth = useMemo(() => addMonths(calendarMonth, 1), [calendarMonth]);
  const rightMonthDays = useMemo(() => buildMonthGrid(rightMonth), [rightMonth]);
  const dateRangeLabel = `${(startDate || defaultRange.start)} to ${(endDate || defaultRange.end)}`;

  const handleDaySelect = useCallback((day) => {
    const dayValue = toDateInputValue(day);
    if (!rangeAnchor) {
      setDraftRange({ start: dayValue, end: dayValue });
      setRangeAnchor(dayValue);
      return;
    }

    const anchorDate = parseDateInput(rangeAnchor);
    if (!anchorDate) {
      setDraftRange({ start: dayValue, end: dayValue });
      setRangeAnchor(dayValue);
      return;
    }

    if (day < anchorDate) {
      setDraftRange({ start: dayValue, end: rangeAnchor });
      setStartDate(dayValue);
      setEndDate(rangeAnchor);
    } else {
      setDraftRange({ start: rangeAnchor, end: dayValue });
      setStartDate(rangeAnchor);
      setEndDate(dayValue);
    }
    setRangeAnchor(null);
    setDatePickerOpen(false);
  }, [rangeAnchor]);

  const renderCalendarMonth = useCallback((monthDate, days) => (
    <div className="logs-range-month">
      <div className="logs-range-month-title">
        {monthDate.toLocaleString([], { month: 'long', year: 'numeric' })}
      </div>
      <div className="logs-range-weekdays">
        {WEEKDAY_LABELS.map((label) => (
          <span key={`${monthDate.getMonth()}-${label}`}>{label}</span>
        ))}
      </div>
      <div className="logs-range-grid">
        {days.map((day) => {
          const inMonth = day.getMonth() === monthDate.getMonth();
          const selectedStart = sameDay(day, activeStartDate);
          const selectedEnd = sameDay(day, activeEndDate);
          const inRange = dayInRange(day, activeStartDate, activeEndDate);
          return (
            <button
              key={day.toISOString()}
              type="button"
              className={[
                'logs-range-day',
                inMonth ? '' : 'outside',
                inRange ? 'in-range' : '',
                selectedStart ? 'range-start' : '',
                selectedEnd ? 'range-end' : '',
              ].filter(Boolean).join(' ')}
              onClick={() => handleDaySelect(day)}
            >
              {day.getDate()}
            </button>
          );
        })}
      </div>
    </div>
  ), [activeEndDate, activeStartDate, handleDaySelect]);

  const selectedGroupLabel = selectedGroup ? formatGroupLabel(selectedGroup) : 'All Agents';
  const selectedFileLabel = selectedFile || 'All Files';

  return (
    <div className="dashboard-container">
      <Header connectionState={connectionState} />
      <main className="logs-main">
        <section className="logs-toolbar-minimal">
          <div className="logs-toolbar-left">
            <div className="logs-toolbar-search">
              <span className="logs-toolbar-icon">⌕</span>
              <input
                type="text"
                placeholder="Search logs..."
                className="logs-search-input"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            <div className="logs-date-range" ref={datePickerRef}>
              <button
                type="button"
                className="logs-date-range-button"
                onClick={() => {
                  setDraftRange({
                    start: startDate || defaultRange.start,
                    end: endDate || defaultRange.end,
                  });
                  setCalendarMonth(monthStart(parseDateInput(startDate || defaultRange.start) || new Date()));
                  setDatePickerOpen((current) => !current);
                }}
              >
                <span className="logs-toolbar-icon">◫</span>
                <span>{dateRangeLabel}</span>
              </button>
              {datePickerOpen ? (
                <div className="logs-date-range-popover">
                  <div className="logs-range-header">
                    <button type="button" className="logs-range-nav" onClick={() => setCalendarMonth((current) => addMonths(current, -1))}>
                      ←
                    </button>
                    <span>Select Date Range</span>
                    <button type="button" className="logs-range-nav" onClick={() => setCalendarMonth((current) => addMonths(current, 1))}>
                      →
                    </button>
                  </div>
                  <div className="logs-range-months">
                    {renderCalendarMonth(calendarMonth, leftMonthDays)}
                    {renderCalendarMonth(rightMonth, rightMonthDays)}
                  </div>
                  <div className="logs-range-footer">
                    <span>{draftRange.start} to {draftRange.end}</span>
                    <button
                      type="button"
                      className="logs-range-clear"
                      onClick={() => {
                        const nextDefault = defaultDateRange();
                        setDraftRange(nextDefault);
                        setStartDate(nextDefault.start);
                        setEndDate(nextDefault.end);
                        setRangeAnchor(null);
                        setDatePickerOpen(false);
                      }}
                    >
                      Last 7 Days
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          </div>
          <div className="logs-toolbar-center">
            <select
              className="logs-select"
              value={selectedGroup}
              onChange={(event) => {
                setSelectedGroup(event.target.value);
                setSelectedFile('');
                setLevel('');
              }}
            >
              <option value="">All Agents</option>
              {sourceGroups.map((group) => (
                <option key={group.id} value={group.id}>
                  {formatGroupLabel(group.label)} ({(group.options || []).length})
                </option>
              ))}
            </select>
            <select
              className="logs-select"
              value={selectedFile}
              onChange={(event) => {
                setSelectedFile(event.target.value);
                setLevel('');
              }}
            >
              <option value="">{selectedGroup ? 'All Files In Agent' : 'All Files'}</option>
              {availableFiles.map((file) => (
                <option key={file.id} value={file.id}>
                  {formatFileLabel(selectedGroup, file)}
                </option>
              ))}
            </select>
          </div>
          <div className="logs-toolbar-stats">
            {[
              ['', 'all', entries.length],
              ['info', 'info', levelCounts.info],
              ['warn', 'warn', levelCounts.warn],
              ['error', 'error', levelCounts.error],
              ['debug', 'debug', levelCounts.debug],
            ].map(([value, label, count]) => (
              <button
                key={label}
                type="button"
                className={`logs-stat-chip ${value || 'all'} ${level === value ? 'active' : ''}`}
                onClick={() => setLevel(level === value ? '' : value)}
              >
                <span>{label}</span>
                <strong>{count}</strong>
              </button>
            ))}
          </div>
          <div className="logs-toolbar-actions">
            <button type="button" className="logs-action-button" onClick={handleExport}>
              Export CSV
            </button>
          </div>
        </section>

        <section className="logs-container-full">
          <div className="detail-header">
            <div className="logs-header-copy">
              <h3>Runtime Logs</h3>
              <span className="badge">{filteredEntries.length}</span>
              <span className="logs-header-meta">{selectedGroupLabel}</span>
              <span className="logs-header-meta">{selectedFileLabel}</span>
            </div>
            <div className="logs-header-actions">
              {!tailPinned && hiddenNewCount > 0 ? (
                <button className="btn btn-primary new-logs-pill" onClick={jumpToNewest}>
                  {hiddenNewCount} new log{hiddenNewCount === 1 ? '' : 's'}
                </button>
              ) : null}
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
                <span className="logs-loading-spinner" aria-hidden="true" />
                <span>Loading logs…</span>
              </div>
            ) : null}
            {!error && !loading && filteredEntries.length === 0 ? (
              <div className="empty-state">
                <span className="empty-icon">📜</span>
                <span>{entries.length > 0 ? 'No logs match the current level filter' : 'No logs available'}</span>
              </div>
            ) : null}
            {!error && filteredEntries.length > 0 ? (
              <div className="logs-virtualized" style={{ height: `${rowLayout.totalHeight}px` }}>
                {visibleRows.map((row) => (
                  <div
                    key={row.id}
                    className={`log-entry-full ${normalizeLevel(row.entry.level) === 'error' ? 'error-entry' : ''} ${row.expanded ? 'expanded' : ''}`}
                    style={{ top: `${row.top}px`, height: `${row.height}px` }}
                  >
                    <div className="log-row-header">
                      <span className="log-timestamp">{formatLogTime(row.entry.time || row.entry.timestamp)}</span>
                      <span className={`log-level-badge ${normalizeLevel(row.entry.level)}`}>
                        {normalizeLevel(row.entry.level)}
                      </span>
                      <span className="log-message-full">{row.entry.message}</span>
                      <span className="log-source">{row.entry.relative_path || row.entry.source || 'system'}</span>
                      {row.entry.data ? (
                        <button className="log-expand-btn" onClick={() => toggleExpanded(row.id)} type="button">
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
