import { useCallback, useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState } from 'react';
import { shallowEqual, useSelector } from 'react-redux';
import { useSearchParams } from 'react-router-dom';
import { useConfig } from '../../context/ConfigContext';
import InlineFailure from '../../components/InlineFailure';
import Select from '../../components/Select';
import { ChevronDownIcon, ChevronRightIcon, CloseIcon, SearchIcon } from '../../components/navIcons';
import { RUN_KINDS, RUN_STATUSES, compareRunRows, normalizeRunPeriodParam, normalizeRunSelection, runMatchesPeriod, runSelectionParam, selectRunTrace, selectRunsSnapshot } from './state';
import { formatDuration, formatTokenCount, kindLabel, runDurationMs } from './runFormat';
import RunTimeline, { ActivityIcon, ActivitySummary, TimeAxis } from './RunTimeline';
import { useRunsQuery, useRunTrace } from './useRunsQuery';
import { runSummarySource, traceBounds } from './timelineModel';
import RunTooltip from './RunTooltip';
import { appendOlderPageRows, mergeRunPages } from './pageModel';
import { subscribeUiEvent } from '../../realtime/realtimeEvents';
import VirtualizedRunRows from './VirtualizedRunRows';

const STATUS_OPTIONS = [{ value: '', label: 'All statuses' }, ...RUN_STATUSES.map((value) => ({ value, label: value[0].toUpperCase() + value.slice(1) }))];
const PERIOD_OPTIONS = [{ value: 'hour', label: '1h' }, { value: 'day', label: '24h' }, { value: 'week', label: '7d' }, { value: 'month', label: '30d' }, { value: '', label: 'All' }];
const KIND_OPTIONS = [{ value: '', label: 'All activity' }, ...RUN_KINDS.map((value) => ({ value, label: kindLabel(value) }))];

function SlidingSelector({ options, value, onChange, label, className = '' }) {
  const selected = Math.max(0, options.findIndex((option) => option.value === value));
  return <div className={`runs-slider ${className}`} role="radiogroup" aria-label={label} style={{ '--segments': options.length, '--selected': selected }}>
    <span className="runs-slider-thumb" aria-hidden="true" />
    {options.map((option, index) => <button key={option.value} type="button" role="radio" aria-checked={value === option.value} tabIndex={index === selected ? 0 : -1} onClick={() => onChange(option.value)} onKeyDown={(event) => {
      let next;
      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % options.length;
      else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index + options.length - 1) % options.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = options.length - 1;
      else return;
      event.preventDefault();
      onChange(options[next].value);
      event.currentTarget.parentElement.querySelectorAll('[role="radio"]')[next].focus();
    }}>{option.label}</button>)}
  </div>;
}

function formatStarted(timestamp) {
  const value = Date.parse(timestamp || '');
  if (!Number.isFinite(value)) return '—';
  return new Date(value).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

function RunRow({ row, expanded, onToggle, now }) {
  const trace = useSelector((state) => selectRunTrace(state, row.kind, row.run_id));
  const summary = useMemo(() => runSummarySource(row, trace, expanded), [row, trace, expanded]);
  const overview = (summary.spans || []).filter((span) => span.kind !== 'run');
  const bounds = useMemo(() => traceBounds(summary, now), [summary, now]);
  const tokens = summary.tokens;
  const status = row.status;
  const root = (summary.spans || []).find((span) => span.kind === 'run' && !span.parent_id);
  const description = [
    row.name,
    root?.summary || null,
    `${kindLabel(row.kind)} · ${row.trigger || 'Unknown trigger'} · ${status}`,
    `Started ${formatStarted(row.started_at)}`,
    row.completed_at ? `Finished ${formatStarted(row.completed_at)}` : null,
    `Run ${row.run_id}`,
    root?.error ? `Error: ${root.error.slice(0, 300)}` : null,
  ].filter(Boolean).join('\n');
  return <div className={`flame-row flame-row--run${expanded ? ' is-expanded' : ''}`} role="row" tabIndex={0} aria-level={1} aria-expanded={expanded} aria-label={`${row.name}, ${status}`} data-tooltip={description} onClick={onToggle} onKeyDown={(event) => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onToggle(); } }}>
    <div className="flame-name" role="rowheader"><button type="button" tabIndex={-1} className="flame-run-button" aria-expanded={expanded} aria-label={`${expanded ? 'Collapse' : 'Expand'} ${row.name}`}>
      {expanded ? <ChevronDownIcon /> : <ChevronRightIcon />}<ActivityIcon kind={row.kind} /><strong className="flame-label">{row.name || 'Unnamed run'}</strong>
    </button></div>
    <div role="cell" className={`flame-status flame-status--${status}`}><span className="flame-status-dot" />{status}</div>
    <div role="cell" className="flame-number flame-started">{formatStarted(row.started_at)}</div>
    <div role="cell" className="flame-number">{runDurationMs(row, now) === null ? '—' : formatDuration(row.started_at, row.completed_at, now)}</div>
    <div role="cell" className="flame-number flame-tokens">{formatTokenCount(tokens)}</div>
    <div role="cell" className={`flame-track flame-track--overview${expanded ? ' flame-track--expanded' : ''}`}>
      <span className="flame-run-band" aria-hidden="true" />
      {expanded ? <TimeAxis bounds={bounds} /> : null}
      <ActivitySummary spans={overview} bounds={bounds} now={now} axis={expanded} />
      {!overview.length ? <span className="flame-summary-empty">No activity timing recorded</span> : null}
    </div>
  </div>;
}

function RunEntry({ row, expanded, trace, traceLoading, traceError, refreshTrace, now, onToggle, inspection, onInspectionChange }) {
  const bounds = useMemo(() => traceBounds(trace || row || {}, now), [now, row, trace]);
  return <div className="runs-entry">
    <RunRow row={row} expanded={expanded} now={now} onToggle={onToggle} />
    {expanded ? <div className="runs-trace">
      {traceError ? <div className="flame-message"><InlineFailure failure={traceError} operation="Load run trace" onRetry={refreshTrace} /></div> : null}
      {traceLoading && !trace ? <div className="flame-message">Loading activity…</div> : null}
      {trace ? <RunTimeline trace={trace} bounds={bounds} now={now} inspection={inspection} onInspectionChange={onInspectionChange} /> : null}
    </div> : null}
  </div>;
}

function SortHeader({ label, active, ascending, onClick }) {
  return <span role="columnheader" aria-sort={active ? (ascending ? 'ascending' : 'descending') : 'none'}>
    <button type="button" className={`runs-sort-header${active ? ' is-active' : ''}`} onClick={onClick}>
      {label}<span aria-hidden="true">{active ? (ascending ? ' ↑' : ' ↓') : ''}</span>
    </button>
  </span>;
}

export default function RunsPage() {
  const { getApiUrl } = useConfig();
  const [searchParams, setSearchParams] = useSearchParams();
  const rootRef = useRef(null);
  const atTopRef = useRef(true);
  const previousRowsRef = useRef(null);
  const [chartScrollElement, setChartScrollElement] = useState(null);
  const [heldRows, dispatchHeldRows] = useReducer((_previous, action) => action, null);
  const [inspections, setInspections] = useState({});
  const [now, setNow] = useState(() => Date.now());
  const kind = RUN_KINDS.includes(searchParams.get('kind')) ? searchParams.get('kind') : '';
  const status = RUN_STATUSES.includes(searchParams.get('status')) ? searchParams.get('status') : '';
  const period = normalizeRunPeriodParam(searchParams.get('period'));
  const search = searchParams.get('q') || '';
  const sort = ['newest', 'oldest', 'duration', 'shortest'].includes(searchParams.get('sort'))
    ? searchParams.get('sort')
    : 'newest';
  const selection = normalizeRunSelection(searchParams.get('run'));
  const filterKey = JSON.stringify([kind, status, period, search, sort]);
  const [paging, setPaging] = useState(() => ({ filterKey, cursors: [null] }));
  const pageCursors = paging.filterKey === filterKey ? paging.cursors : [null];
  const inspectionKey = filterKey;
  const [query, setQuery] = useState(search);
  const updateInspection = useCallback((key, change) => {
    setInspections((current) => {
      const next = change(current[key]);
      return current[key] === next ? current : { ...current, [key]: next };
    });
  }, []);
  const updateParams = useCallback((changes, { replace = false } = {}) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      for (const [key, value] of Object.entries(changes)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      return next;
    }, { replace });
  }, [setSearchParams]);
  const resetRunView = useCallback((changes, options) => {
    dispatchHeldRows(null);
    chartScrollElement?.scrollTo({ top: 0 });
    updateParams({ ...changes, run: '' }, options);
  }, [chartScrollElement, updateParams]);
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search), 200);
    return () => window.clearTimeout(timer);
  }, [search]);

  const baseQuery = useMemo(() => ({ kind, status, q: query, period, sort }), [kind, period, query, sort, status]);
  const activeCursor = pageCursors.at(-1) || null;
  const { snapshot, loading, error, refresh, streamReady } = useRunsQuery({
    runsUrl: getApiUrl('runs'), ...baseQuery, cursor: activeCursor,
  });
  useEffect(() => subscribeUiEvent((event) => {
    if (event.type !== 'connection.ready') return;
    // Cursor values describe the former page boundary. A ready event starts a
    // fresh authoritative window, while URL selection and per-run inspection
    // state deliberately remain independent from its records.
    previousRowsRef.current = null;
    dispatchHeldRows(null);
    setPaging((current) => (
      current.filterKey === filterKey && current.cursors.length === 1 && current.cursors[0] === null
        ? current
        : { filterKey, cursors: [null] }
    ));
  }), [filterKey]);
  const pageSnapshots = useSelector((state) => pageCursors.map((cursor) => (
    selectRunsSnapshot(state, { ...baseQuery, cursor })
  )), shallowEqual);
  const serverRows = useMemo(() => mergeRunPages(
    pageSnapshots,
    (left, right) => compareRunRows(left, right, sort),
  ), [pageSnapshots, sort]);
  const rows = useMemo(
    () => serverRows.filter((row) => runMatchesPeriod(row, period, now)),
    [now, period, serverRows],
  );
  const loadedPageCount = pageSnapshots.filter(Boolean).length;
  const activeHeldRows = heldRows?.inspectionKey === inspectionKey ? heldRows : null;
  useLayoutEffect(() => {
    const previous = previousRowsRef.current;
    if (!previous || previous.inspectionKey !== inspectionKey) {
      previousRowsRef.current = { inspectionKey, rows, loadedPageCount };
      return;
    }
    if (previous.loadedPageCount !== loadedPageCount) {
      if (activeHeldRows && loadedPageCount > previous.loadedPageCount) {
        dispatchHeldRows({
          ...activeHeldRows,
          rows: appendOlderPageRows(activeHeldRows.rows, pageSnapshots, previous.loadedPageCount, rows),
          loadedPageCount,
        });
      }
      previousRowsRef.current = { inspectionKey, rows, loadedPageCount };
      return;
    }
    if (previous.rows !== rows && !atTopRef.current) {
      const latestChanged = rows.length !== previous.rows.length
        || rows.some((row, index) => row !== previous.rows[index]);
      if (latestChanged) {
        const baselineRows = activeHeldRows?.rows || previous.rows;
        const baselineByKey = new Map(baselineRows.map((row) => [runSelectionParam(row.kind, row.run_id), row]));
        const newRunCount = rows.filter((row) => !baselineByKey.has(runSelectionParam(row.kind, row.run_id))).length;
        dispatchHeldRows({ inspectionKey, rows: baselineRows, newRunCount, loadedPageCount });
      }
    }
    previousRowsRef.current = { inspectionKey, rows, loadedPageCount };
  }, [activeHeldRows, inspectionKey, loadedPageCount, pageSnapshots, rows]);
  const visibleRows = useMemo(() => {
    if (!activeHeldRows) return rows;
    const currentByKey = new Map(rows.map((row) => [runSelectionParam(row.kind, row.run_id), row]));
    return activeHeldRows.rows.map((row) => currentByKey.get(runSelectionParam(row.kind, row.run_id)) || row);
  }, [activeHeldRows, rows]);
  const selectedRow = visibleRows.find((row) => row.kind === selection?.kind && row.run_id === selection?.runId) || null;
  const { trace, loading: traceLoading, error: traceError, refresh: refreshTrace } = useRunTrace({
    runsUrl: getApiUrl('runs'),
    subscriptionUrl: getApiUrl('runSubscription'),
    kind: selection?.kind,
    runId: selection?.runId,
    enabled: Boolean(selection),
  });
  const renderedRows = useMemo(() => {
    if (!selection || selectedRow || !trace) return visibleRows;
    return [...visibleRows, trace];
  }, [selectedRow, selection, trace, visibleRows]);
  const virtualRows = useMemo(() => renderedRows.map((row) => ({
    key: runSelectionParam(row.kind, row.run_id),
    row,
    expanded: row.kind === selection?.kind && row.run_id === selection?.runId,
  })), [renderedRows, selection]);
  const hasLive = visibleRows.some((row) => ['running', 'queued', 'pending', 'paused', 'started'].includes(row.status));
  useEffect(() => {
    if (!hasLive && !period) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), hasLive ? 1000 : 60_000);
    return () => window.clearInterval(timer);
  }, [hasLive, period]);
  const toggleRun = useCallback((row) => {
    const value = runSelectionParam(row.kind, row.run_id);
    updateParams({ run: value === searchParams.get('run') ? '' : value });
  }, [searchParams, updateParams]);
  const revealNewRuns = useCallback(() => {
    dispatchHeldRows(null);
    chartScrollElement?.scrollTo({ top: 0, behavior: 'smooth' });
  }, [chartScrollElement]);
  const nextCursor = snapshot?.next_cursor || null;
  const loadNextPage = useCallback(() => {
    if (!nextCursor || loading) return;
    setPaging((current) => {
      const cursors = current.filterKey === filterKey ? current.cursors : [null];
      return cursors.includes(nextCursor)
        ? current
        : { filterKey, cursors: [...cursors, nextCursor] };
    });
  }, [filterKey, loading, nextCursor]);
  const handleScroll = useCallback((event) => {
    const { clientHeight, scrollHeight, scrollTop } = event.currentTarget;
    const isAtTop = scrollTop <= 8;
    atTopRef.current = isAtTop;
    if (isAtTop) dispatchHeldRows(null);
    if (scrollHeight - scrollTop - clientHeight < 360) loadNextPage();
  }, [loadNextPage]);
  const updateStartedSort = useCallback(() => resetRunView({ sort: sort === 'newest' ? 'oldest' : 'newest' }), [resetRunView, sort]);
  const updateDurationSort = useCallback(() => resetRunView({ sort: sort === 'duration' ? 'shortest' : 'duration' }), [resetRunView, sort]);
  useEffect(() => {
    if (!chartScrollElement || loading || !nextCursor) return undefined;
    if (chartScrollElement.scrollHeight - chartScrollElement.scrollTop - chartScrollElement.clientHeight >= 360) {
      return undefined;
    }
    const frame = requestAnimationFrame(loadNextPage);
    return () => cancelAnimationFrame(frame);
  }, [chartScrollElement, loadNextPage, loading, nextCursor]);


  return <main ref={rootRef} className="runs-page" aria-label="Runs">
    <section className="runs-controls" aria-label="Run filters">
      <SlidingSelector options={PERIOD_OPTIONS} value={period} onChange={(value) => { setNow(Date.now()); resetRunView({ period: value || 'all' }); }} label="Run time range" className="runs-time-slider" />
      <label className="runs-search"><SearchIcon /><span className="sr-only">Search runs</span><input value={search} onChange={(event) => resetRunView({ q: event.target.value }, { replace: true })} placeholder="Search runs" />{search ? <button type="button" className="flame-icon-button" aria-label="Clear search" data-tooltip="Clear search" onClick={() => resetRunView({ q: '' })}><CloseIcon /></button> : null}</label>
    </section>
    <div ref={setChartScrollElement} className="runs-chart-scroll" onScroll={handleScroll}>
      {activeHeldRows ? <div className="runs-new-runs-anchor"><button type="button" className="runs-new-runs" onClick={revealNewRuns}>{activeHeldRows.newRunCount ? `${activeHeldRows.newRunCount} new ${activeHeldRows.newRunCount === 1 ? 'run' : 'runs'}` : 'Updates available'}</button></div> : null}
      <div className="runs-chart" role="treegrid" aria-label="Run execution timelines" aria-colcount={6}>
        <div className="flame-head" role="row">
          <div role="columnheader" className="runs-header-filter"><Select portal value={kind} onChange={(value) => resetRunView({ kind: value })} options={KIND_OPTIONS} ariaLabel="Filter activity kind" /></div>
          <div role="columnheader" className="runs-header-filter"><Select portal value={status} onChange={(value) => resetRunView({ status: value })} options={STATUS_OPTIONS} ariaLabel="Filter run status" /></div>
          <SortHeader label="Started" active={sort === 'newest' || sort === 'oldest'} ascending={sort === 'oldest'} onClick={updateStartedSort} />
          <SortHeader label="Duration" active={sort === 'duration' || sort === 'shortest'} ascending={sort === 'shortest'} onClick={updateDurationSort} />
          <span role="columnheader" className="flame-number">Tokens</span>
          <span role="columnheader" className="flame-timeline-heading">Timeline <small>elapsed per run</small></span>
        </div>
        {!streamReady ? <div className="flame-message">Connecting…</div> : null}
        {loading && !pageSnapshots.some(Boolean) ? <div className="flame-message">Loading runs…</div> : null}
        {!loading && pageSnapshots.some(Boolean) && !visibleRows.length && !selection ? <div className="flame-message">No runs match these filters.</div> : null}
        <VirtualizedRunRows rows={virtualRows} scrollElement={chartScrollElement} renderRow={({ row, expanded }) => {
          const key = runSelectionParam(row.kind, row.run_id);
          return <RunEntry row={row} expanded={expanded} trace={expanded ? trace : null} traceLoading={expanded && traceLoading} traceError={expanded ? traceError : null} refreshTrace={refreshTrace} now={now} onToggle={() => toggleRun(row)} inspection={inspections[key]} onInspectionChange={(change) => updateInspection(key, change)} />;
        }} />
        {error ? <div className="runs-page-status"><InlineFailure failure={error} operation="Load runs" onRetry={refresh} /></div> : null}
        {loading && pageSnapshots.some(Boolean) ? <div className="runs-page-status" role="status">Loading more runs…</div> : null}
        {snapshot && !snapshot.next_cursor && visibleRows.length ? <div className="runs-page-status" role="status">End of run history</div> : null}
      </div>
    </div>
    <RunTooltip rootRef={rootRef} />
  </main>;
}
