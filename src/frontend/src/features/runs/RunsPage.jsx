import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { useSelector } from 'react-redux';
import { useConfig } from '../../context/ConfigContext';
import InlineFailure from '../../components/InlineFailure';
import Select from '../../components/Select';
import { ChevronDownIcon, ChevronLeftIcon, ChevronRightIcon, CloseIcon, RunsNavIcon, SearchIcon } from '../../components/navIcons';
import { RUN_KINDS, RUN_STATUSES, runMatchesPeriod, selectRunTrace } from './state';
import { formatDuration, formatRelativeTime, formatTokenCount, kindLabel } from './runFormat';
import RunTimeline, { ActivityIcon, ActivitySummary, TimeAxis } from './RunTimeline';
import { useRunsQuery, useRunTrace } from './useRunsQuery';
import { runSummarySource, traceBounds } from './timelineModel';
import RunTooltip from './RunTooltip';

const STATUS_OPTIONS = [{ value: '', label: 'All statuses' }, ...RUN_STATUSES.map((value) => ({ value, label: value[0].toUpperCase() + value.slice(1) }))];
const SORT_OPTIONS = [{ value: 'newest', label: 'Newest first' }, { value: 'oldest', label: 'Oldest first' }, { value: 'duration', label: 'Longest first' }];
const PERIOD_OPTIONS = [{ value: 'hour', label: 'Hour' }, { value: 'day', label: 'Day' }, { value: 'week', label: 'Week' }, { value: 'month', label: 'Month' }, { value: '', label: 'All time' }];

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
  const rootRef = useRef(null);
  const [kind, setKind] = useState('');
  const [status, setStatus] = useState('');
  const [period, setPeriod] = useState('week');
  const [search, setSearch] = useState('');
  const [expandedKey, setExpandedKey] = useState(null);
  const [cursors, setCursors] = useState([null]);
  const [sort, setSort] = useState('newest');
  const [now, setNow] = useState(() => Date.now());
  const [query, setQuery] = useState('');
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search), 200);
    return () => window.clearTimeout(timer);
  }, [search]);
  const { snapshot, loading, error, refresh, streamReady } = useRunsQuery({ runsUrl: getApiUrl('runs'), kind, status, q: query, cursor: cursors.at(-1) || null, period });
  const rows = useMemo(() => (snapshot?.runs || []).filter((row) => runMatchesPeriod(row, period, now)).sort((left, right) => {
    if (sort === 'duration') return (Date.parse(right.completed_at || '') || now) - Date.parse(right.started_at || '') - ((Date.parse(left.completed_at || '') || now) - Date.parse(left.started_at || ''));
    const compared = (left.started_at || '').localeCompare(right.started_at || '') || left.run_id.localeCompare(right.run_id);
    return sort === 'oldest' ? compared : -compared;
  }), [snapshot?.runs, sort, now, period]);
  const expandedRow = rows.find((row) => `${row.kind}:${row.run_id}` === expandedKey) || null;
  if (expandedKey && snapshot && !loading && !expandedRow) setExpandedKey(null);
  const { trace, loading: traceLoading, error: traceError, refresh: refreshTrace } = useRunTrace({ runsUrl: getApiUrl('runs'), subscriptionUrl: getApiUrl('runSubscription'), kind: expandedRow?.kind, runId: expandedRow?.run_id, enabled: Boolean(expandedRow) });
  const bounds = useMemo(() => traceBounds(trace || expandedRow || {}, now), [trace, expandedRow, now]);
  const hasLive = rows.some((row) => ['running', 'queued', 'pending', 'paused', 'started'].includes(row.status));
  useEffect(() => {
    if (!hasLive && !period) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), hasLive ? 1000 : 60_000);
    return () => window.clearInterval(timer);
  }, [hasLive, period]);
  const resetPaging = (change) => { setExpandedKey(null); setCursors([null]); change(); };
  const changePage = (change) => { setExpandedKey(null); change(); };

  return <main ref={rootRef} className="runs-page" aria-label="Runs">
    <section className="runs-controls" aria-label="Run filters">
      <div className="runs-filter-group" aria-label="Run kind"><button type="button" className={!kind ? 'is-selected' : ''} aria-pressed={!kind} onClick={() => resetPaging(() => setKind(''))}><RunsNavIcon />All</button>{RUN_KINDS.map((value) => <button key={value} type="button" className={kind === value ? 'is-selected' : ''} aria-pressed={kind === value} onClick={() => resetPaging(() => setKind(value))}><ActivityIcon kind={value} />{kindLabel(value)}</button>)}</div>
      <Select value={status} onChange={(value) => resetPaging(() => setStatus(value))} options={STATUS_OPTIONS} ariaLabel="Run status" className="runs-status-select" />
      <Select value={period} onChange={(value) => resetPaging(() => { setPeriod(value); setNow(Date.now()); })} options={PERIOD_OPTIONS} ariaLabel="Run time range" className="runs-period-select" />
      <label className="runs-search"><SearchIcon /><span className="sr-only">Search runs</span><input value={search} onChange={(event) => resetPaging(() => setSearch(event.target.value))} placeholder="Search runs" />{search ? <button type="button" className="flame-icon-button" aria-label="Clear search" data-tooltip="Clear search" onClick={() => resetPaging(() => setSearch(''))}><CloseIcon /></button> : null}</label>
      <Select value={sort} onChange={setSort} options={SORT_OPTIONS} ariaLabel="Sort loaded runs" className="runs-sort-select" />
      <nav className="runs-pagination" aria-label="Runs pages"><button type="button" className="flame-icon-button" disabled={cursors.length === 1 || loading} data-tooltip="Previous page" aria-label="Previous page" onClick={() => changePage(() => setCursors((items) => items.slice(0, -1)))}><ChevronLeftIcon /></button><span data-tooltip={`Page ${cursors.length} · Sorting applies to the loaded page`}>{cursors.length}</span><button type="button" className="flame-icon-button" disabled={!snapshot?.next_cursor || loading} data-tooltip="Next page" aria-label="Next page" onClick={() => changePage(() => setCursors((items) => [...items, snapshot.next_cursor]))}><ChevronRightIcon /></button></nav>
    </section>
    {error ? <InlineFailure failure={error} operation="Load runs" onRetry={refresh} /> : null}
    {!streamReady ? <div className="flame-message">Connecting…</div> : null}
    {loading && !snapshot ? <div className="flame-message">Loading runs…</div> : null}
    {snapshot && !rows.length && !loading ? <div className="flame-message">No runs match these filters.</div> : null}
    <div className="runs-chart-scroll"><div className="runs-chart" role="treegrid" aria-label="Run execution timelines" aria-colcount={5}>
      <div className="flame-head" role="row"><span role="columnheader">Activity</span><span role="columnheader">Status</span><span role="columnheader" className="flame-number">Duration</span><span role="columnheader" className="flame-number">Tokens</span><span role="columnheader" className="flame-timeline-heading">Timeline <small>elapsed per run</small></span></div>
      {rows.map((row) => {
        const key = `${row.kind}:${row.run_id}`;
        const expanded = key === expandedKey;
        return <Fragment key={key}><RunRow row={row} expanded={expanded} now={now} onToggle={() => setExpandedKey(expanded ? null : key)} />
          {expanded ? <Fragment>
            {traceError ? <div className="flame-message"><InlineFailure failure={traceError} operation="Load run trace" onRetry={refreshTrace} /></div> : null}
            {traceLoading && !trace ? <div className="flame-message">Loading activity…</div> : null}
            {trace ? <RunTimeline key={key} trace={trace} bounds={bounds} now={now} /> : null}
          </Fragment> : null}
        </Fragment>;
      })}
    </div></div>
    <RunTooltip rootRef={rootRef} />
  </main>;
}
