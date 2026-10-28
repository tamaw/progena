import { Fragment, useDeferredValue, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useConfig } from '../../context/ConfigContext';
import InlineFailure from '../../components/InlineFailure';
import RunStatusPill from '../../components/RunStatusPill';
import { RUN_KINDS, RUN_STATUSES } from './state';
import { formatDuration, formatRelativeTime, kindLabel, progressLabel, progressPercent } from './runFormat';
import { RunTraceContent } from './TraceWaterfall';
import { useRunsQuery } from './useRunsQuery';

const SORT_COLUMNS = {
  run: (row) => `${row.name || ''}\u0000${row.kind || ''}`,
  trigger: (row) => row.trigger || '',
  status: (row) => row.status || '',
  progress: (row) => progressPercent(row) ?? -1,
  duration: (row) => Date.parse(row.completed_at || '') - Date.parse(row.started_at || '') || 0,
  started: (row) => Date.parse(row.started_at || '') || 0,
};

function KindMark({ kind }) {
  return <span className={`runs-kind-mark runs-kind-mark--${kind}`} title={kindLabel(kind)} aria-label={kindLabel(kind)}>{kind === 'workflow' ? '◇' : kind === 'schedule' ? '◷' : '⌁'}</span>;
}

function SortHeader({ label, field, sort, onSort }) {
  const active = sort.field === field;
  return <th scope="col" aria-sort={active ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
    <button type="button" className="runs-sort" onClick={() => onSort(field)}>{label}<span aria-hidden="true">{active ? (sort.direction === 'asc' ? ' ↑' : ' ↓') : ''}</span></button>
  </th>;
}

function Progress({ row }) {
  const percent = progressPercent(row);
  if (percent === null) return <span className="runs-muted">—</span>;
  return <div className="runs-progress" title={`Estimated ${progressLabel(row)} executable steps`}>
    <span className="runs-progress__bar"><span style={{ width: `${percent}%` }} /></span><span>{progressLabel(row)}</span>
  </div>;
}

export default function RunsPage() {
  const { getApiUrl } = useConfig();
  const [kind, setKind] = useState('');
  const [status, setStatus] = useState('');
  const [search, setSearch] = useState('');
  const [expandedKey, setExpandedKey] = useState(null);
  const deferredSearch = useDeferredValue(search);
  const [cursors, setCursors] = useState([null]);
  const [sort, setSort] = useState({ field: 'started', direction: 'desc' });
  const cursor = cursors.at(-1) || null;
  const { snapshot, loading, error, refresh, streamReady } = useRunsQuery({
    runsUrl: getApiUrl('runs'), kind, status, q: deferredSearch, cursor,
  });

  const rows = useMemo(() => [...(snapshot?.runs || [])].sort((left, right) => {
    const value = SORT_COLUMNS[sort.field](left);
    const compared = typeof value === 'string'
      ? value.localeCompare(SORT_COLUMNS[sort.field](right))
      : value - SORT_COLUMNS[sort.field](right);
    return sort.direction === 'asc' ? compared : -compared;
  }), [snapshot?.runs, sort]);
  const expandedRow = rows.find((row) => `${row.kind}:${row.run_id}` === expandedKey) || null;

  if (expandedKey && snapshot && !loading && !expandedRow) setExpandedKey(null);

  const resetPaging = (change) => {
    setExpandedKey(null);
    setCursors([null]);
    change();
  };
  const toggleSort = (field) => setSort((current) => current.field === field
    ? { field, direction: current.direction === 'asc' ? 'desc' : 'asc' }
    : { field, direction: field === 'started' ? 'desc' : 'asc' });
  const toggleExpanded = (row) => {
    const key = `${row.kind}:${row.run_id}`;
    setExpandedKey((current) => current === key ? null : key);
  };
  const changePage = (change) => {
    setExpandedKey(null);
    change();
  };

  return <main className="runs-page" aria-label="Runs">
    <section className="runs-controls" aria-label="Run filters">
      <div className="runs-filter-group" aria-label="Run kind">
        <button type="button" className={!kind ? 'is-selected' : ''} aria-pressed={!kind} onClick={() => resetPaging(() => setKind(''))}>All</button>
        {RUN_KINDS.map((value) => <button key={value} type="button" className={kind === value ? 'is-selected' : ''} aria-pressed={kind === value} onClick={() => resetPaging(() => setKind(value))}>{kindLabel(value)}</button>)}
      </div>
      <label className="runs-filter-select">Status<select value={status} onChange={(event) => resetPaging(() => setStatus(event.target.value))}>
        <option value="">All statuses</option>{RUN_STATUSES.map((value) => <option key={value} value={value}>{value}</option>)}
      </select></label>
      <span className="runs-sort-scope">Column sorting applies to this page</span>
      <label className="runs-search"><span className="sr-only">Search runs</span><input value={search} onChange={(event) => resetPaging(() => setSearch(event.target.value))} placeholder="Search runs" /></label>
    </section>
    {error ? <InlineFailure failure={error} operation="Load runs" onRetry={refresh} /> : null}
    {!streamReady ? <div className="runs-state">Connecting…</div> : null}
    {loading && !snapshot ? <div className="runs-state">Loading runs…</div> : null}
    {snapshot && !rows.length && !loading ? <div className="runs-state">No runs match these filters.</div> : null}
    {snapshot && rows.length ? <div className="runs-table-wrap"><table className="runs-table"><caption>Runs use newest-first server paging. Column sorting applies to the loaded page.</caption><thead><tr>
      <SortHeader label="Run" field="run" sort={sort} onSort={toggleSort} /><SortHeader label="Trigger" field="trigger" sort={sort} onSort={toggleSort} />
      <SortHeader label="Status" field="status" sort={sort} onSort={toggleSort} /><SortHeader label="Progress" field="progress" sort={sort} onSort={toggleSort} />
      <SortHeader label="Duration" field="duration" sort={sort} onSort={toggleSort} /><SortHeader label="Started" field="started" sort={sort} onSort={toggleSort} />
    </tr></thead><tbody>{rows.map((row) => {
      const rowKey = `${row.kind}:${row.run_id}`;
      const expanded = rowKey === expandedKey;
      return <Fragment key={rowKey}>
        <tr tabIndex={0} role="button" aria-expanded={expanded} aria-controls={`run-trace-${row.kind}-${row.run_id}`} onClick={() => toggleExpanded(row)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggleExpanded(row); } }}>
          <td><div className="runs-name"><span className="runs-row-toggle" aria-hidden="true">{expanded ? '▾' : '▸'}</span><KindMark kind={row.kind} /><strong title={row.name}>{row.name || 'Unnamed run'}</strong></div></td>
          <td>{row.trigger || <span className="runs-muted">—</span>}</td><td><RunStatusPill status={row.status} /></td><td><Progress row={row} /></td>
          <td>{formatDuration(row.started_at, row.completed_at)}</td><td title={row.started_at || ''}>{formatRelativeTime(row.started_at)}</td>
        </tr>
        {expanded ? <tr className="runs-expanded-row"><td colSpan={6}><div id={`run-trace-${row.kind}-${row.run_id}`} className="runs-inline-trace"><div className="runs-inline-trace__bar"><span>Trace</span><Link to={`/runs/${encodeURIComponent(row.kind)}/${encodeURIComponent(row.run_id)}`} onClick={(event) => event.stopPropagation()}>Open trace</Link></div><RunTraceContent kind={row.kind} runId={row.run_id} inline /></div></td></tr> : null}
      </Fragment>;
    })}</tbody></table></div> : null}
    {snapshot ? <nav className="runs-pagination" aria-label="Runs pages"><button type="button" className="task-btn task-btn--secondary" disabled={cursors.length === 1 || loading} onClick={() => changePage(() => setCursors((items) => items.slice(0, -1)))}>Previous</button><span>Page {cursors.length}</span><button type="button" className="task-btn task-btn--secondary" disabled={!snapshot.next_cursor || loading} onClick={() => changePage(() => setCursors((items) => [...items, snapshot.next_cursor]))}>Next</button></nav> : null}
  </main>;
}
