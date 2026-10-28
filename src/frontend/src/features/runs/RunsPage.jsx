import { Fragment, useEffect, useMemo, useState } from 'react';
import { useSelector } from 'react-redux';
import { useConfig } from '../../context/ConfigContext';
import InlineFailure from '../../components/InlineFailure';
import Select from '../../components/Select';
import { ChevronDownIcon, ChevronLeftIcon, ChevronRightIcon, CloseIcon, RunsNavIcon, SearchIcon } from '../../components/navIcons';
import { RUN_KINDS, RUN_STATUSES, selectRunTrace } from './state';
import { formatDuration, formatRelativeTime, formatTokenCount, kindLabel, progressLabel } from './runFormat';
import RunTimeline, { ActivityIcon, TimeAxis } from './RunTimeline';
import { useRunsQuery, useRunTrace } from './useRunsQuery';
import { spanGeometry, traceBounds } from './timelineModel';

const STATUS_OPTIONS = [{ value: '', label: 'All statuses' }, ...RUN_STATUSES.map((value) => ({ value, label: value[0].toUpperCase() + value.slice(1) }))];
const SORT_OPTIONS = [{ value: 'newest', label: 'Newest first' }, { value: 'oldest', label: 'Oldest first' }, { value: 'duration', label: 'Longest first' }];

function RunRow({ row, expanded, onToggle, now }) {
  const trace = useSelector((state) => selectRunTrace(state, row.kind, row.run_id));
  const bounds = useMemo(() => traceBounds(trace || row, now), [trace, row, now]);
  const status = row.status;
  return <div className={`flame-row flame-row--run${expanded ? ' is-expanded' : ''}`} role="row" aria-level={1} aria-expanded={expanded}>
    <div className="flame-name" role="rowheader"><button type="button" className="flame-run-button" onClick={onToggle} aria-expanded={expanded} aria-label={`${expanded ? 'Collapse' : 'Expand'} ${row.name}`}>
      {expanded ? <ChevronDownIcon /> : <ChevronRightIcon />}<ActivityIcon kind={row.kind} /><strong className="flame-label" title={row.name}>{row.name || 'Unnamed run'}</strong>
    </button><span className="flame-run-caption" title={`${row.trigger} · ${row.started_at || 'Not started'} · Estimated ${progressLabel(row)} steps`}>{progressLabel(row)}<span> · {formatRelativeTime(row.started_at)}</span></span></div>
    <div role="cell" className={`flame-status flame-status--${status}`}><span className="flame-status-dot" />{status}</div>
    <div role="cell" className="flame-number">{formatDuration(row.started_at, row.completed_at, now)}</div>
    <div role="cell" className="flame-number flame-tokens" title={trace?.tokens == null ? 'Expand run to load recorded usage' : `${trace.tokens.toLocaleString()} recorded tokens`}>{formatTokenCount(trace?.tokens)}</div>
    {expanded ? <TimeAxis bounds={bounds} /> : <div role="cell" className="flame-track flame-track--overview"><button type="button" className="flame-run-band" onClick={onToggle} aria-label={`Expand timeline for ${row.name}`} title={`${row.name} · ${formatDuration(row.started_at, row.completed_at, now)}`} />{trace?.spans?.filter((span) => ['model', 'tool'].includes(span.kind)).map((span) => {
      const geometry = spanGeometry(span, bounds, now);
      return <span key={span.id} className={`flame-overview-event flame-bar--${span.kind}${geometry.unavailable ? ' flame-bar--marker' : ''}`} style={{ left: `${geometry.left}%`, width: `${geometry.width}%` }} />;
    })}</div>}
  </div>;
}

export default function RunsPage() {
  const { getApiUrl } = useConfig();
  const [kind, setKind] = useState('');
  const [status, setStatus] = useState('');
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
  const { snapshot, loading, error, refresh, streamReady } = useRunsQuery({ runsUrl: getApiUrl('runs'), kind, status, q: query, cursor: cursors.at(-1) || null });
  const rows = useMemo(() => [...(snapshot?.runs || [])].sort((left, right) => {
    if (sort === 'duration') return (Date.parse(right.completed_at || '') || now) - Date.parse(right.started_at || '') - ((Date.parse(left.completed_at || '') || now) - Date.parse(left.started_at || ''));
    const compared = (left.started_at || '').localeCompare(right.started_at || '') || left.run_id.localeCompare(right.run_id);
    return sort === 'oldest' ? compared : -compared;
  }), [snapshot?.runs, sort, now]);
  const expandedRow = rows.find((row) => `${row.kind}:${row.run_id}` === expandedKey) || null;
  if (expandedKey && snapshot && !loading && !expandedRow) setExpandedKey(null);
  const { trace, loading: traceLoading, error: traceError, refresh: refreshTrace } = useRunTrace({ runsUrl: getApiUrl('runs'), subscriptionUrl: getApiUrl('runSubscription'), kind: expandedRow?.kind, runId: expandedRow?.run_id, enabled: Boolean(expandedRow) });
  const bounds = useMemo(() => traceBounds(trace || expandedRow || {}, now), [trace, expandedRow, now]);
  const hasLive = rows.some((row) => ['running', 'queued', 'pending', 'paused', 'started'].includes(row.status));
  useEffect(() => {
    if (!hasLive) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [hasLive]);
  const resetPaging = (change) => { setExpandedKey(null); setCursors([null]); change(); };
  const changePage = (change) => { setExpandedKey(null); change(); };

  return <main className="runs-page" aria-label="Runs">
    <section className="runs-controls" aria-label="Run filters">
      <div className="runs-filter-group" aria-label="Run kind"><button type="button" className={!kind ? 'is-selected' : ''} aria-pressed={!kind} onClick={() => resetPaging(() => setKind(''))}><RunsNavIcon />All</button>{RUN_KINDS.map((value) => <button key={value} type="button" className={kind === value ? 'is-selected' : ''} aria-pressed={kind === value} onClick={() => resetPaging(() => setKind(value))}><ActivityIcon kind={value} />{kindLabel(value)}</button>)}</div>
      <Select value={status} onChange={(value) => resetPaging(() => setStatus(value))} options={STATUS_OPTIONS} ariaLabel="Run status" className="runs-status-select" />
      <label className="runs-search"><SearchIcon /><span className="sr-only">Search runs</span><input value={search} onChange={(event) => resetPaging(() => setSearch(event.target.value))} placeholder="Search runs" />{search ? <button type="button" className="flame-icon-button" aria-label="Clear search" title="Clear search" onClick={() => resetPaging(() => setSearch(''))}><CloseIcon /></button> : null}</label>
      <Select value={sort} onChange={setSort} options={SORT_OPTIONS} ariaLabel="Sort loaded runs" className="runs-sort-select" />
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
    <footer className="runs-footer"><div className="runs-legend" aria-label="Timeline legend"><span className="flame-kind--step">Step</span><span className="flame-kind--agent">Agent</span><span className="flame-kind--model">Model</span><span className="flame-kind--tool">Tool</span><span className="runs-legend-note">Markers: timing unavailable · Tokens load on expansion</span></div>
      <nav className="runs-pagination" aria-label="Runs pages"><button type="button" className="flame-icon-button" disabled={cursors.length === 1 || loading} title="Previous page" aria-label="Previous page" onClick={() => changePage(() => setCursors((items) => items.slice(0, -1)))}><ChevronLeftIcon /></button><span title="Newest-first server paging; sorting applies to the loaded page">Page {cursors.length}</span><button type="button" className="flame-icon-button" disabled={!snapshot?.next_cursor || loading} title="Next page" aria-label="Next page" onClick={() => changePage(() => setCursors((items) => [...items, snapshot.next_cursor]))}><ChevronRightIcon /></button></nav>
    </footer>
  </main>;
}
