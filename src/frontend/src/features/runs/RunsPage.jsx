import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState } from 'react';
import { useSelector } from 'react-redux';
import { useSearchParams } from 'react-router-dom';
import { useConfig } from '../../context/ConfigContext';
import InlineFailure from '../../components/InlineFailure';
import Select from '../../components/Select';
import { ChevronDownIcon, ChevronLeftIcon, ChevronRightIcon, CloseIcon, RunsNavIcon, SearchIcon } from '../../components/navIcons';
import { RUN_KINDS, RUN_STATUSES, normalizeRunPeriodParam, normalizeRunSelection, runMatchesPeriod, runSelectionParam, selectRunTrace } from './state';
import { formatDuration, formatRelativeTime, formatTokenCount, kindLabel } from './runFormat';
import RunTimeline, { ActivityIcon, ActivitySummary, TimeAxis } from './RunTimeline';
import { useRunsQuery, useRunTrace } from './useRunsQuery';
import { runSummarySource, traceBounds } from './timelineModel';
import RunTooltip from './RunTooltip';

const STATUS_OPTIONS = [{ value: '', label: 'All statuses' }, ...RUN_STATUSES.map((value) => ({ value, label: value[0].toUpperCase() + value.slice(1) }))];
const SORT_OPTIONS = [{ value: 'newest', label: 'Latest' }, { value: 'oldest', label: 'Oldest' }, { value: 'duration', label: 'Longest' }];
const PERIOD_OPTIONS = [{ value: 'hour', label: '1h' }, { value: 'day', label: '24h' }, { value: 'week', label: '7d' }, { value: 'month', label: '30d' }, { value: '', label: 'All' }];
const KIND_OPTIONS = [{ value: '', label: <><RunsNavIcon />All</> }, ...RUN_KINDS.map((value) => ({ value, label: <><ActivityIcon kind={value} />{kindLabel(value)}</> }))];

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
    `Started ${row.started_at ? new Date(row.started_at).toLocaleString(undefined, { timeZoneName: 'short' }) : 'not recorded'}`,
    row.completed_at ? `Finished ${new Date(row.completed_at).toLocaleString(undefined, { timeZoneName: 'short' })}` : null,
    `Run ${row.run_id}`,
    root?.error ? `Error: ${root.error.slice(0, 300)}` : null,
  ].filter(Boolean).join('\n');
  return <div className={`flame-row flame-row--run${expanded ? ' is-expanded' : ''}`} role="row" tabIndex={0} aria-level={1} aria-expanded={expanded} aria-label={`${row.name}, ${status}`} data-tooltip={description} onClick={onToggle} onKeyDown={(event) => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onToggle(); } }}>
    <div className="flame-name" role="rowheader"><button type="button" tabIndex={-1} className="flame-run-button" aria-expanded={expanded} aria-label={`${expanded ? 'Collapse' : 'Expand'} ${row.name}`}>
      {expanded ? <ChevronDownIcon /> : <ChevronRightIcon />}<ActivityIcon kind={row.kind} /><strong className="flame-label">{row.name || 'Unnamed run'}</strong>
    </button><span className="flame-run-caption">{formatRelativeTime(row.started_at)}</span></div>
    <div role="cell" className={`flame-status flame-status--${status}`}><span className="flame-status-dot" />{status}</div>
    <div role="cell" className="flame-number">{formatDuration(row.started_at, row.completed_at, now)}</div>
    <div role="cell" className="flame-number flame-tokens" data-tooltip={tokens == null ? 'No recorded token usage' : summary === trace ? `${tokens.toLocaleString()} recorded tokens · inclusive of child activity` : `${tokens.toLocaleString()} tokens recorded in execution metadata.\nExpand for per-model usage; additional calls may increase this total.`}>{formatTokenCount(tokens)}</div>
    <div role="cell" className={`flame-track flame-track--overview${expanded ? ' flame-track--expanded' : ''}`}>
      <span className="flame-run-band" aria-hidden="true" />
      {expanded ? <TimeAxis bounds={bounds} /> : null}
      <ActivitySummary spans={overview} bounds={bounds} now={now} axis={expanded} />
      {!overview.length ? <span className="flame-summary-empty">No activity timing recorded</span> : null}
    </div>
  </div>;
}

export default function RunsPage() {
  const { getApiUrl } = useConfig();
  const [searchParams, setSearchParams] = useSearchParams();
  const rootRef = useRef(null);
  const chartScrollRef = useRef(null);
  const atTopRef = useRef(true);
  const previousRowsRef = useRef(null);
  const [heldRows, dispatchHeldRows] = useReducer((_previous, action) => action, null);
  const [now, setNow] = useState(() => Date.now());
  const kind = RUN_KINDS.includes(searchParams.get('kind')) ? searchParams.get('kind') : '';
  const status = RUN_STATUSES.includes(searchParams.get('status')) ? searchParams.get('status') : '';
  const period = normalizeRunPeriodParam(searchParams.get('period'));
  const search = searchParams.get('q') || '';
  const sort = SORT_OPTIONS.some((option) => option.value === searchParams.get('sort'))
    ? searchParams.get('sort')
    : 'newest';
  const selection = normalizeRunSelection(searchParams.get('run'));
  const filterKey = JSON.stringify([kind, status, period, search, sort]);
  const [paging, setPaging] = useState(() => ({ filterKey, cursors: [null] }));
  const pageCursors = paging.filterKey === filterKey ? paging.cursors : [null];
  const inspectionKey = JSON.stringify([filterKey, pageCursors.at(-1)]);
  const [query, setQuery] = useState(search);
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
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search), 200);
    return () => window.clearTimeout(timer);
  }, [search]);
  const { snapshot, loading, error, refresh, streamReady } = useRunsQuery({
    runsUrl: getApiUrl('runs'), kind, status, q: query, cursor: pageCursors.at(-1) || null, period, sort,
  });
  const serverRows = useMemo(() => snapshot?.runs || [], [snapshot]);
  const rows = useMemo(
    () => serverRows.filter((row) => runMatchesPeriod(row, period, now)),
    [now, period, serverRows],
  );
  const activeHeldRows = heldRows?.inspectionKey === inspectionKey ? heldRows : null;
  useLayoutEffect(() => {
    const previous = previousRowsRef.current;
    if (!previous || previous.inspectionKey !== inspectionKey) {
      previousRowsRef.current = { inspectionKey, rows };
      return;
    }
    if (previous.rows !== rows && !atTopRef.current) {
      const latestChanged = rows.length !== previous.rows.length
        || rows.some((row, index) => row !== previous.rows[index]);
      if (latestChanged) {
        const baselineRows = activeHeldRows?.rows || previous.rows;
        const baselineByKey = new Map(baselineRows.map((row) => [runSelectionParam(row.kind, row.run_id), row]));
        const newRunCount = rows.filter((row) => !baselineByKey.has(runSelectionParam(row.kind, row.run_id))).length;
        dispatchHeldRows({ inspectionKey, rows: baselineRows, newRunCount });
      }
    }
    previousRowsRef.current = { inspectionKey, rows };
  }, [activeHeldRows, inspectionKey, rows]);
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
  const expandedRow = selectedRow || trace;
  const bounds = useMemo(() => traceBounds(trace || expandedRow || {}, now), [trace, expandedRow, now]);
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
    chartScrollRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
  }, []);
  const handleScroll = useCallback((event) => {
    const isAtTop = event.currentTarget.scrollTop <= 8;
    atTopRef.current = isAtTop;
    if (isAtTop) dispatchHeldRows(null);
  }, []);

  return <main ref={rootRef} className="runs-page" aria-label="Runs">
    <section className="runs-controls" aria-label="Run filters">
      <SlidingSelector options={KIND_OPTIONS} value={kind} onChange={(value) => updateParams({ kind: value })} label="Run kind" className="runs-kind-slider" />
      <Select value={status} onChange={(value) => updateParams({ status: value })} options={STATUS_OPTIONS} ariaLabel="Run status" className="runs-status-select" />
      <SlidingSelector options={PERIOD_OPTIONS} value={period} onChange={(value) => { setNow(Date.now()); updateParams({ period: value || 'all' }); }} label="Run time range" className="runs-time-slider" />
      <label className="runs-search"><SearchIcon /><span className="sr-only">Search runs</span><input value={search} onChange={(event) => updateParams({ q: event.target.value }, { replace: true })} placeholder="Search runs" />{search ? <button type="button" className="flame-icon-button" aria-label="Clear search" data-tooltip="Clear search" onClick={() => updateParams({ q: '' })}><CloseIcon /></button> : null}</label>
      <SlidingSelector options={SORT_OPTIONS} value={sort} onChange={(value) => updateParams({ sort: value })} label="Sort runs" className="runs-sort-slider" />
      <nav className="runs-pagination" aria-label="Runs pages"><button type="button" className="flame-icon-button" disabled={pageCursors.length === 1 || loading} data-tooltip="Previous page" aria-label="Previous page" onClick={() => setPaging((current) => (current.filterKey === filterKey ? { ...current, cursors: current.cursors.slice(0, -1) } : current))}><ChevronLeftIcon /></button><span data-tooltip={`Page ${pageCursors.length} · Sorted across all results`}>{pageCursors.length}</span><button type="button" className="flame-icon-button" disabled={!snapshot?.next_cursor || loading} data-tooltip="Next page" aria-label="Next page" onClick={() => setPaging({ filterKey, cursors: [...pageCursors, snapshot.next_cursor] })}><ChevronRightIcon /></button></nav>
    </section>
    {error ? <InlineFailure failure={error} operation="Load runs" onRetry={refresh} /> : null}
    {!streamReady ? <div className="flame-message">Connecting…</div> : null}
    {loading && !snapshot ? <div className="flame-message">Loading runs…</div> : null}
    {snapshot && !visibleRows.length && !selection && !loading ? <div className="flame-message">No runs match these filters.</div> : null}
    <div ref={chartScrollRef} className="runs-chart-scroll" onScroll={handleScroll}>
      {activeHeldRows ? <div className="runs-new-runs-anchor"><button type="button" className="runs-new-runs" onClick={revealNewRuns}>{activeHeldRows.newRunCount ? `${activeHeldRows.newRunCount} new ${activeHeldRows.newRunCount === 1 ? 'run' : 'runs'}` : 'Updates available'}</button></div> : null}
      <div className="runs-chart" role="treegrid" aria-label="Run execution timelines" aria-colcount={5}>
        <div className="flame-head" role="row"><span role="columnheader">Activity</span><span role="columnheader">Status</span><span role="columnheader" className="flame-number">Duration</span><span role="columnheader" className="flame-number">Tokens</span><span role="columnheader" className="flame-timeline-heading">Timeline <small>elapsed per run</small></span></div>
        {visibleRows.map((row) => {
          const key = runSelectionParam(row.kind, row.run_id);
          const expanded = key === searchParams.get('run');
          return <Fragment key={key}><RunRow row={row} expanded={expanded} now={now} onToggle={() => toggleRun(row)} />
            {expanded ? <Fragment>
              {traceError ? <div className="flame-message"><InlineFailure failure={traceError} operation="Load run trace" onRetry={refreshTrace} /></div> : null}
              {traceLoading && !trace ? <div className="flame-message">Loading activity…</div> : null}
              {trace ? <RunTimeline key={key} trace={trace} bounds={bounds} now={now} /> : null}
            </Fragment> : null}
          </Fragment>;
        })}
        {selection && !selectedRow ? <Fragment>
          {!trace && traceError ? <div className="flame-message"><InlineFailure failure={traceError} operation="Load run trace" onRetry={refreshTrace} /></div> : null}
          {!trace && traceLoading ? <div className="flame-message">Loading selected run…</div> : null}
          {trace ? <Fragment><RunRow row={trace} expanded now={now} onToggle={() => toggleRun(trace)} />
            {traceError ? <div className="flame-message"><InlineFailure failure={traceError} operation="Load run trace" onRetry={refreshTrace} /></div> : null}
            <RunTimeline key={runSelectionParam(trace.kind, trace.run_id)} trace={trace} bounds={bounds} now={now} />
          </Fragment> : null}
        </Fragment> : null}
      </div>
    </div>
    <RunTooltip rootRef={rootRef} />
  </main>;
}
