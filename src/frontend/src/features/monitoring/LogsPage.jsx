/* eslint-disable react-hooks/set-state-in-effect -- this page synchronizes URL, stream, and scroll state. */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useSelector } from 'react-redux';
import { useSearchParams } from 'react-router-dom';
import { useConfig } from '../../context/ConfigContext';
import { useLogsApi } from './useLogsApi';
import { selectLogsExport, selectLogsPage, selectLogsState } from './logsState';
import { requestAcknowledged, withQuery } from '../../store/http';
import { selectRealtime } from '../../realtime/realtimeSlice';
import { createUuid } from '../../lib/uuid';
import Select from '../../components/Select';
import InlineFailure from '../../components/InlineFailure';
import { failureMessage, reportFailure } from '../../lib/failures';
import { useNotifications } from '../../context/NotificationContext';

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

function formatSourceMeta(file) {
  const pieces = [];
  if (file.size) {
    pieces.push(file.size);
  }
  if (file.updated) {
    pieces.push(file.updated);
  }
  return pieces.join(' / ') || 'Log file';
}

function mergeEntries(currentEntries, incomingEntries) {
  if (!incomingEntries.length) {
    return currentEntries;
  }
  const seen = new Set(currentEntries.map((entry) => getEntryId(entry)));
  const freshEntries = incomingEntries.filter((entry) => !seen.has(getEntryId(entry)));
  return freshEntries.length > 0 ? [...freshEntries, ...currentEntries] : currentEntries;
}

function exportEntriesToCsv(entries) {
  const fields = ['timestamp', 'level', 'source', 'relative_path', 'message', 'data'];
  const quote = (value) => `"${String(value ?? '').replaceAll('"', '""')}"`;
  return [
    fields.join(','),
    ...entries.map((entry) => fields.map((field) => (
      field === 'data' ? quote(JSON.stringify(entry[field] ?? null)) : quote(entry[field])
    )).join(',')),
  ].join('\n');
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
  const { getLogs } = useLogsApi();
  const { getApiUrl } = useConfig();
  const { show } = useNotifications();
  const logsPage = useSelector(selectLogsPage);
  const logsState = useSelector(selectLogsState);
  const [searchParams, setSearchParams] = useSearchParams();
  const defaultRange = useMemo(() => defaultDateRange(), []);
  const [entries, setEntries] = useState([]);
  const sourceGroups = logsState.sourceGroups;
  const [selectedGroup, setSelectedGroup] = useState(searchParams.get('group') || '');
  const [selectedFile, setSelectedFile] = useState(searchParams.get('file') || '');
  const [search, setSearch] = useState(searchParams.get('search') || '');
  const [level, setLevel] = useState(searchParams.get('level') || '');
  const [startDate, setStartDate] = useState(searchParams.get('start') || '');
  const [endDate, setEndDate] = useState(searchParams.get('end') || '');
  const [loadingEntries, setLoadingEntries] = useState(true);
  const [error, setError] = useState('');
  const [pendingLogRequest, setPendingLogRequest] = useState(null);
  const [pendingExportRequestId, setPendingExportRequestId] = useState(null);
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
  const sourceOptions = useMemo(() => {
    const options = [{
      value: 'all',
      group: '',
      file: '',
      label: 'All log streams',
      detail: 'Every runtime file',
    }];

    sourceGroups.forEach((group) => {
      const groupLabel = formatGroupLabel(group.label || group.id);
      const files = group.options || [];
      options.push({
        value: `group:${group.id}`,
        group: group.id,
        file: '',
        label: groupLabel,
        detail: `${files.length} file${files.length === 1 ? '' : 's'}`,
      });
      files.forEach((file) => {
        const fileLabel = formatFileLabel(group.id, file);
        options.push({
          value: `file:${group.id}:${file.id}`,
          group: group.id,
          file: file.id,
          label: `${groupLabel} / ${fileLabel}`,
          detail: file.path || formatSourceMeta(file),
        });
      });
    });

    return options;
  }, [sourceGroups]);

  const sourceSelectOptions = useMemo(
    () => sourceOptions.map((option) => ({
      value: option.value,
      label: option.label + (option.detail ? ` - ${option.detail}` : ''),
    })),
    [sourceOptions],
  );

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
    const items = [];
    let totalHeight = 0;
    for (const entry of filteredEntries) {
      const id = getEntryId(entry);
      const expanded = expandedIds.has(id);
      const height = expanded ? EXPANDED_ROW_HEIGHT : COLLAPSED_ROW_HEIGHT;
      items.push({ entry, id, top: totalHeight, height, expanded });
      totalHeight += height;
    }
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
      const acknowledgement = await getLogs(buildQueryParams(requestFilters, {
        format: 'json',
        after: incremental ? newestCursorRef.current || undefined : undefined,
        limit: incremental ? INCREMENTAL_LIMIT : 0,
      }));
      if (requestVersion === requestVersionRef.current) {
        setPendingLogRequest({
          requestId: acknowledgement.request_id,
          incremental,
          requestVersion,
        });
      }
    } catch (loadError) {
      if (requestVersion === requestVersionRef.current) {
        setError(failureMessage(loadError, 'Load logs'));
        if (!incremental) {
          setLoadingEntries(false);
        }
      }
    }
  }, [getLogs, requestFilters]);

  useEffect(() => {
    if (!pendingLogRequest || logsState.requestId !== pendingLogRequest.requestId || !logsPage) {
      return;
    }
    if (pendingLogRequest.requestVersion !== requestVersionRef.current) {
      setPendingLogRequest(null);
      return;
    }
    const page = logsPage;
    if (!pendingLogRequest.incremental && page.date_range) {
      setStartDate((current) => current || page.date_range.start || defaultRange.start);
      setEndDate((current) => current || page.date_range.end || defaultRange.end);
    }

    if (!pendingLogRequest.incremental && !defaultSelectionAppliedRef.current && !selectedGroup && !selectedFile) {
      const activeSelection = page.active_selection || {};
      if (activeSelection.group) {
        setSelectedGroup(activeSelection.group);
      }
      if (activeSelection.file) {
        setSelectedFile(activeSelection.file);
      }
      defaultSelectionAppliedRef.current = true;
    }

    if (pendingLogRequest.incremental) {
      if (!page.cursor_found) {
        setPendingLogRequest(null);
        void loadLogs({ incremental: false });
        return;
      }
      const incomingEntries = Array.isArray(page.entries) ? page.entries : [];
      if (incomingEntries.length > 0) {
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
      }
      setNewestCursor(page.newest_cursor || newestCursorRef.current);
      setPendingLogRequest(null);
      return;
    }

    setEntries(Array.isArray(page.entries) ? page.entries : []);
    setNewestCursor(page.newest_cursor || '');
    setExpandedIds(new Set());
    setLoadingEntries(false);
    setPendingLogRequest(null);
  }, [defaultRange.end, defaultRange.start, loadLogs, logsPage, logsState.requestId, pendingLogRequest, selectedFile, selectedGroup]);

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

  const realtimeConnected = useSelector(selectRealtime).connected;

  useEffect(() => {
    if (!realtimeConnected) {
      return;
    }
    if (newestCursorRef.current) {
      queueIncrementalRefresh();
    } else {
      void loadLogs({ incremental: false });
    }
  }, [realtimeConnected, loadLogs, queueIncrementalRefresh]);
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

  const handleExport = useCallback(async () => {
    const requestId = createUuid();
    const params = buildQueryParams(requestFilters, { format: 'csv', ...(level ? { level } : {}) });
    try {
      const acknowledgement = await requestAcknowledged(
        withQuery(getApiUrl('/api/ui/logs/export'), params),
        { requestId },
      );
      setPendingExportRequestId(acknowledgement.request_id);
    } catch (exportError) {
      reportFailure(show, 'Export logs', exportError);
    }
  }, [getApiUrl, level, requestFilters, show]);

  const exportState = useSelector((state) => selectLogsExport(state, pendingExportRequestId));
  useEffect(() => {
    if (!pendingExportRequestId || !exportState?.completed) {
      return;
    }
    const exportedEntries = exportState.chunks.flatMap((chunk) => chunk || []);
    const blob = new Blob([exportEntriesToCsv(exportedEntries)], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'progenitor-logs.csv';
    anchor.click();
    URL.revokeObjectURL(url);
    setPendingExportRequestId(null);
  }, [exportState, pendingExportRequestId]);

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

  const selectedGroupLabel = selectedGroup ? formatGroupLabel(selectedGroup) : 'All sources';
  const selectedFileLabel = selectedFile || 'All files';
  const selectedSourceOption = sourceOptions.find((option) => (
    option.group === selectedGroup && option.file === selectedFile
  )) || sourceOptions.find((option) => option.group === selectedGroup && !option.file) || sourceOptions[0];
  const selectedSourceValue = selectedSourceOption?.value || 'all';
  const logFileCount = sourceGroups.reduce((total, group) => total + (group.options || []).length, 0);
  const levelFilterOptions = [
    { value: '', label: 'All', count: entries.length },
    { value: 'info', label: 'Info', count: levelCounts.info, color: '#79c0ff' },
    { value: 'warn', label: 'Warn', count: levelCounts.warn, color: '#f2c94c' },
    { value: 'error', label: 'Errors', count: levelCounts.error, color: 'var(--color-error)', tone: 'err' },
    { value: 'debug', label: 'Debug', count: levelCounts.debug, color: '#b1bac4' },
  ];

  return (
    <div className="dashboard-container">
      <main className="logs-main">
        <div className="logs-workspace">
          <section className="logs-toolbar-minimal">
            <div className="logs-toolbar-primary">
              <div className="logs-toolbar-search">
                <span className="logs-toolbar-icon">⌕</span>
                <input
                  type="text"
                  placeholder="Search logs..."
                  className="logs-search-input"
                  aria-label="Search logs"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </div>
              <div className="logs-source-select">
                <Select
                  value={selectedSourceValue}
                  onChange={(next) => {
                    const option = sourceOptions.find((item) => item.value === next) || sourceOptions[0];
                    setSelectedGroup(option.group);
                    setSelectedFile(option.file);
                    setLevel('');
                  }}
                  options={sourceSelectOptions}
                  searchable
                  ariaLabel="Log source"
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
              <button type="button" className="logs-action-button" onClick={handleExport}>
                Export CSV
              </button>
            </div>
            <div className="logs-filter-row" role="group" aria-label="Log filters">
              <div className="logs-toolbar-stats" role="group" aria-label="Log level">
                {levelFilterOptions.map((filter) => (
                  <button
                    key={filter.label}
                    type="button"
                    className={`sess-fchip logs-stat-chip ${level === filter.value ? 'is-active' : ''} ${filter.tone === 'err' ? 'sess-fchip--err' : ''}`}
                    aria-pressed={level === filter.value}
                    onClick={() => setLevel(level === filter.value ? '' : filter.value)}
                  >
                    {filter.color ? <span className="adot" style={{ background: filter.color }} /> : null}
                    {filter.label}
                    <span className="count">{filter.count.toLocaleString()}</span>
                  </button>
                ))}
              </div>
              <div className="logs-toolbar-meta">
                <span>{filteredEntries.length.toLocaleString()} visible</span>
                <span>{entries.length.toLocaleString()} loaded</span>
                <span>{logFileCount.toLocaleString()} files</span>
                <span>{selectedSourceOption?.label || `${selectedGroupLabel} / ${selectedFileLabel}`}</span>
                {!tailPinned && hiddenNewCount > 0 ? (
                  <button className="btn btn-primary new-logs-pill" onClick={jumpToNewest} type="button">
                    {hiddenNewCount} new log{hiddenNewCount === 1 ? '' : 's'}
                  </button>
                ) : null}
              </div>
            </div>
          </section>

          <section className="logs-container-full">
            <div ref={scrollAreaRef} className="logs-scroll-area" onScroll={handleScroll}>
            <InlineFailure failure={error} onRetry={() => loadLogs()} />
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
            {filteredEntries.length > 0 ? (
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
        </div>
      </main>
    </div>
  );
}

export default LogsPage;
