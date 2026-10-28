import { useEffect, useMemo, useState } from 'react';
import { useConfig } from '../../context/ConfigContext';
import InlineFailure from '../../components/InlineFailure';
import RunStatusPill from '../../components/RunStatusPill';
import { useRunTrace } from './useRunsQuery';
import { formatDuration, formatTokenCount } from './runFormat';

const EMPTY_SPANS = Object.freeze([]);

function toTimestamp(value, fallback) {
  const parsed = Date.parse(value || '');
  return Number.isFinite(parsed) ? parsed : fallback;
}

function flattenSpans(spans, collapsed) {
  const byParent = new Map();
  const ids = new Set(spans.map((span) => span.id));
  for (const span of spans) {
    const parent = span.parent_id && ids.has(span.parent_id) ? span.parent_id : null;
    byParent.set(parent, [...(byParent.get(parent) || []), span]);
  }
  const rows = [];
  const visit = (parent, depth) => {
    for (const span of byParent.get(parent) || []) {
      const children = byParent.get(span.id) || [];
      rows.push({ span, depth, hasChildren: children.length > 0 });
      if (!collapsed[span.id]) visit(span.id, depth + 1);
    }
  };
  visit(null, 0);
  return rows;
}

function tickLabel(milliseconds) {
  if (milliseconds < 1000) return `${Math.round(milliseconds)}ms`;
  if (milliseconds < 60000) return `${(milliseconds / 1000).toFixed(1)}s`;
  return `${Math.floor(milliseconds / 60000)}m`;
}

function traceIsLive(status) {
  return !['completed', 'failed', 'cancelled', 'stopped', 'error', 'interrupted']
    .includes(String(status || '').toLowerCase());
}

function spanDurationLabel(span, isLive, now) {
  if (span.timing_source === 'unavailable') return '—';
  return span.end || isLive ? formatDuration(span.start, span.end || new Date(now).toISOString(), now) : '—';
}


function SpanDetails({ span, isLive, now, onClose }) {
  const summary = typeof span.summary === 'string' ? span.summary : span.summary ? JSON.stringify(span.summary, null, 2) : null;
  const error = typeof span.error === 'string' ? span.error : span.error ? JSON.stringify(span.error, null, 2) : null;
  return <aside className="trace-details" aria-label="Selected span details">
    <header><div><span>Selected span</span><h2>{span.name || span.operation || 'Unnamed span'}</h2></div><div className="trace-details__heading-actions"><span className={`trace-kind trace-kind--${span.kind}`}>{span.kind}</span><button type="button" className="trace-details__close" onClick={onClose} aria-label="Close span details">×</button></div></header>
    <dl><div><dt>Operation</dt><dd>{span.operation || '—'}</dd></div><div><dt>Duration</dt><dd>{spanDurationLabel(span, isLive, now)}</dd></div><div><dt>Trace tokens</dt><dd>{formatTokenCount(span.tokens)}</dd></div>{span.timing_source ? <div><dt>Timing</dt><dd>{span.timing_source}</dd></div> : null}</dl>
    {summary ? <section><h3>Input / output summary</h3><pre>{summary}</pre></section> : null}
    {error ? <section className="trace-details__error"><h3>Error</h3><pre>{error}</pre></section> : null}
    {!summary && !error ? <p className="runs-muted">No input, output, or error summary was recorded.</p> : null}
  </aside>;
}

export function RunTraceContent({ kind, runId, inline = false }) {
  const { getApiUrl } = useConfig();
  const { trace, loading, error, refresh, streamReady } = useRunTrace({
    runsUrl: getApiUrl('runs'),
    subscriptionUrl: getApiUrl('runSubscription'),
    kind, runId, enabled: true,
  });

  return <section className={`run-trace-content${inline ? ' run-trace-content--inline' : ''}`} aria-label="Run trace">
    {trace ? <div className="run-trace-content__meta"><RunStatusPill status={trace.status} /><span>{formatDuration(trace.started_at, trace.completed_at)}</span><span>{formatTokenCount(trace.tokens)} trace tokens</span></div> : null}
    {error ? <InlineFailure failure={error} operation="Load run trace" onRetry={refresh} /> : null}
    {!streamReady ? <div className="runs-state">Connecting…</div> : null}
    {loading && !trace ? <div className="runs-state">Loading trace…</div> : null}
    {trace ? <TraceWaterfall trace={trace} /> : null}
  </section>;
}

export default function TraceWaterfall({ trace }) {
  const [now, setNow] = useState(() => Date.now());
  const [collapsed, setCollapsed] = useState({});
  const [selectedId, setSelectedId] = useState(null);
  const spans = Array.isArray(trace?.spans) ? trace.spans : EMPTY_SPANS;
  const isLive = traceIsLive(trace?.status);
  useEffect(() => {
    if (!isLive) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [isLive]);
  const bounds = useMemo(() => {
    const starts = spans.map((span) => toTimestamp(span.start, NaN)).filter(Number.isFinite);
    const knownEnds = spans.map((span) => toTimestamp(span.end, NaN)).filter(Number.isFinite);
    const traceStart = toTimestamp(trace?.started_at, NaN);
    const start = Math.min(...[traceStart, ...starts].filter(Number.isFinite), now);
    const completed = toTimestamp(trace?.completed_at, NaN);
    const terminalEnd = Math.max(...[completed, ...knownEnds, start + 1].filter(Number.isFinite));
    const end = isLive ? Math.max(terminalEnd, now) : terminalEnd;
    return { start, end, duration: Math.max(1, end - start) };
  }, [isLive, now, spans, trace?.completed_at, trace?.started_at]);
  const rows = useMemo(() => flattenSpans(spans, collapsed), [collapsed, spans]);
  const selected = spans.find((span) => span.id === selectedId) || null;
  const ticks = [0, 0.25, 0.5, 0.75, 1];

  if (!spans.length) return <div className="runs-state">No trace spans have been recorded for this run.</div>;
  return <div className={`trace-layout${selected ? '' : ' trace-layout--unselected'}`}>
    <section className="trace-waterfall" aria-label="Run trace waterfall">
      <div className="trace-head"><span>Span</span><span>Operation</span><div className="trace-axis">{ticks.map((fraction) => <span key={fraction} style={{ left: `${fraction * 100}%` }}>{tickLabel(bounds.duration * fraction)}</span>)}</div></div>
      <div className="trace-body">{rows.map(({ span, depth, hasChildren }) => {
        const start = toTimestamp(span.start, bounds.start);
        const end = span.timing_source === 'unavailable'
          ? start
          : span.end ? toTimestamp(span.end, start) : isLive ? now : start;
        const left = Math.max(0, Math.min(100, ((start - bounds.start) / bounds.duration) * 100));
        const selectedRow = selectedId === span.id;
        const width = Math.max(0, Math.min(100 - left, ((Math.max(start, end) - start) / bounds.duration) * 100));
        return <div key={span.id} className={`trace-row${selectedRow ? ' is-selected' : ''}`}>
          <div className="trace-span" style={{ paddingLeft: `${12 + depth * 18}px` }}>
            {hasChildren ? <button type="button" className="trace-collapse" onClick={() => setCollapsed((value) => ({ ...value, [span.id]: !value[span.id] }))} aria-label={`${collapsed[span.id] ? 'Expand' : 'Collapse'} ${span.name || 'span'}`}>{collapsed[span.id] ? '›' : '⌄'}</button> : <span className={`trace-dot trace-dot--${span.kind}`} />}
            <button type="button" className="trace-span__select" onClick={() => setSelectedId(span.id)} aria-pressed={selectedRow}>{span.name || 'Unnamed span'}</button>
          </div>
          <button type="button" className="trace-operation" onClick={() => setSelectedId(span.id)}>{span.operation || '—'}</button>
          <button type="button" className="trace-timeline" onClick={() => setSelectedId(span.id)} aria-label={`${span.name || 'Span'}, ${spanDurationLabel(span, isLive, now)}`}>
            <span className="trace-bar-grid" />
            <span className={`trace-bar trace-bar--${span.kind}${span.error ? ' trace-bar--error' : ''}`} style={{ left: `${left}%`, width: `${width}%` }}><span>{spanDurationLabel(span, isLive, now)}</span></span>
          </button>
        </div>;
      })}</div>
    </section>
    {selected ? <SpanDetails span={selected} isLive={isLive} now={now} onClose={() => setSelectedId(null)} /> : null}
  </div>;
}
