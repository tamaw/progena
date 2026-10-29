import { Fragment, useMemo } from 'react';
import { ChevronDownIcon, ChevronRightIcon, AgentsNavIcon, ModelsNavIcon, RoutinesNavIcon, TasksNavIcon, ToolsNavIcon, SchedulesNavIcon } from '../../components/navIcons';
import Select from '../../components/Select';
import { formatDuration, formatTokenCount } from './runFormat';
import { activityRollups, spanGeometry, timelineRows, tickLabel } from './timelineModel';
const EMPTY_INSPECTION = Object.freeze({});

export function ActivityIcon({ kind }) {
  const Icon = ({ workflow: TasksNavIcon, routine: RoutinesNavIcon, schedule: SchedulesNavIcon, agent: AgentsNavIcon, model: ModelsNavIcon, tool: ToolsNavIcon })[kind] || TasksNavIcon;
  return <span className={`flame-kind flame-kind--${kind}`}><Icon /></span>;
}

export function DetailsIcon() {
  return <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><path d="M3 3.5h10M3 8h10M3 12.5h6" /><path d="m11 11 2 2 2-2" /></svg>;
}

export function TimeAxis({ bounds }) {
  return <div className="flame-axis" aria-hidden="true">
    {[0, .25, .5, .75, 1].map((fraction) => <span key={fraction} style={{ left: `${fraction * 100}%` }}>{tickLabel(bounds.duration * fraction)}</span>)}
  </div>;
}

export function ActivitySummary({ spans, bounds, now, axis = false }) {
  const lanes = { step: 0, queue: 0, routine: 1, agent: 1, model: 2, tool: 3 };
  return <span className="flame-summary" aria-hidden="true">
    {spans.filter((span) => span.kind !== 'run').map((span) => {
      const geometry = spanGeometry(span, bounds, now);
      return <span key={span.id} className={`flame-summary-event flame-bar--${span.kind}${span.error ? ' flame-bar--error' : ''}${geometry.unavailable ? ' flame-bar--marker' : ''}`} style={{ left: `${geometry.left}%`, width: `${geometry.width}%`, top: `${(axis ? 17 : 6) + (lanes[span.kind] || 0) * 4}px` }} />;
    })}
  </span>;
}

function spanDuration(span, bounds, now) {
  if (span.timing_source === 'unavailable' || (!span.end && !bounds.isLive)) return '—';
  return formatDuration(span.start, span.end, now);
}

function rowTokens(spans, rollups) {
  const values = spans.map((span) => rollups.get(span.id)?.tokens).filter((value) => typeof value === 'number');
  return values.length ? values.reduce((sum, value) => sum + value, 0) : null;
}

function SpanDisclosure({ row, selectedId, onSelect }) {
  const span = row.spans.find((item) => item.id === selectedId) || row.spans[0];
  return <div className="flame-details" role="region" aria-label={`Details for ${span.name}`}>
    {row.spans.length > 1 ? <Select className="flame-detail-calls" ariaLabel="Call details" value={span.id} onChange={onSelect} options={row.spans.map((item, index) => ({ value: item.id, label: `${index + 1}. ${item.name}` }))} /> : null}
    <div className="flame-detail-heading"><strong>{span.name}</strong><code>{span.operation || span.kind}</code><span>{span.timing_source === 'unavailable' ? 'Timing unavailable' : span.timing_source === 'checkpoint' ? 'Checkpoint-derived timing' : span.timing_source}</span></div>
    {span.summary ? <pre>{span.summary}</pre> : null}
    {span.error ? <pre className="flame-detail-error">{span.error}</pre> : null}
    {!span.summary && !span.error ? <p>No input, output, or error summary was recorded.</p> : null}
  </div>;
}

export default function RunTimeline({ trace, bounds, now, inspection, onInspectionChange }) {
  const collapsed = inspection?.collapsed || EMPTY_INSPECTION;
  const selected = inspection?.selected || EMPTY_INSPECTION;
  const detailsId = inspection?.detailsId || null;
  const updateInspection = (change) => onInspectionChange((current) => change(current || EMPTY_INSPECTION));
  const rows = useMemo(() => timelineRows(trace.spans || [], collapsed), [trace.spans, collapsed]);
  const rollups = useMemo(() => activityRollups(trace.spans || []), [trace.spans]);
  if (!rows.length) return <div className="flame-message">No trace activity recorded.</div>;
  return rows.map((row) => {
    const first = row.spans[0];
    const selectedId = selected[row.id] || first.id;
    const detailOpen = detailsId === row.id;
    const tokens = rowTokens(row.spans, rollups);
    const failed = row.spans.some((span) => span.error || ['failed', 'error'].includes(span.status));
    const live = row.spans.some((span) => !span.end && span.timing_source !== 'unavailable') && bounds.isLive;
    const duration = row.spans.length === 1 ? spanDuration(first, bounds, now) : `${row.spans.length} calls`;
    return <Fragment key={row.id}>
      <div className={`flame-row flame-row--span${row.hasChildren ? ' flame-row--parent' : ''}${detailOpen ? ' is-disclosed' : ''}`} role="row" aria-level={row.depth + 2}>
        <div className="flame-name" role="rowheader" style={{ paddingLeft: `${12 + (row.depth + 1) * 14}px` }}>
          {row.hasChildren ? <button type="button" className="flame-icon-button" onClick={() => updateInspection((current) => ({ ...current, collapsed: { ...(current.collapsed || {}), [row.id]: !collapsed[row.id] } }))} aria-label={`${collapsed[row.id] ? 'Expand' : 'Collapse'} ${row.name}`} data-tooltip={`${collapsed[row.id] ? 'Expand' : 'Collapse'} ${row.name}`} aria-expanded={!collapsed[row.id]}>{collapsed[row.id] ? <ChevronRightIcon /> : <ChevronDownIcon />}</button> : <span className="flame-indent" />}
          <ActivityIcon kind={row.kind} /><span className="flame-label" data-tooltip={row.name}>{row.name}</span>
          <button type="button" className={`flame-icon-button flame-details-toggle${detailOpen ? ' is-selected' : ''}`} aria-label={`${detailOpen ? 'Hide' : 'Show'} details for ${row.name}`} aria-expanded={detailOpen} aria-controls={`span-details-${first.id}`} data-tooltip="Toggle call details" onClick={() => updateInspection((current) => ({ ...current, detailsId: current.detailsId === row.id ? null : row.id }))}><DetailsIcon /></button>
        </div>
        <div className={`flame-status${failed ? ' flame-status--failed' : ''}`} role="cell">{failed ? 'failed' : live ? 'running' : row.spans.length > 1 ? row.kind : first.status}</div>
        <div className="flame-number flame-started" role="cell">—</div>
        <div className="flame-number" role="cell">{duration}</div>
        <div className="flame-number flame-tokens" role="cell" data-tooltip={tokens === null ? 'No recorded token usage' : `${tokens.toLocaleString()} recorded tokens${row.hasChildren ? ' · inclusive of child activity' : ''}`}>{formatTokenCount(tokens)}</div>
        <div className="flame-track" role="cell">{row.spans.map((span) => {
          const geometry = spanGeometry(span, bounds, now);
          const chosen = selected[row.id] === span.id;
          return <button key={span.id} type="button" className={`flame-bar flame-bar--${span.kind}${geometry.unavailable ? ' flame-bar--marker' : ''}${span.error ? ' flame-bar--error' : ''}${chosen ? ' is-selected' : ''}`} style={{ left: `${geometry.left}%`, width: `${geometry.width}%` }} data-tooltip={`${span.name} · ${spanDuration(span, bounds, now)} · ${formatTokenCount(rollups.get(span.id)?.tokens)} tokens${geometry.unavailable ? ' · timing unavailable' : ''}`} aria-label={`Select ${span.name}, ${spanDuration(span, bounds, now)}`} aria-pressed={chosen} onClick={() => updateInspection((current) => ({ ...current, selected: { ...(current.selected || {}), [row.id]: span.id } }))}><span>{span.name}</span></button>;
        })}{row.hasChildren ? <ActivitySummary spans={rollups.get(first.id)?.descendants || []} bounds={bounds} now={now} /> : null}</div>
      </div>
      {detailOpen ? <div id={`span-details-${first.id}`} className="flame-detail-row" role="row"><div role="cell" aria-colspan={6}><SpanDisclosure row={row} selectedId={selectedId} onSelect={(id) => updateInspection((current) => ({ ...current, selected: { ...(current.selected || {}), [row.id]: id } }))} /></div></div> : null}
    </Fragment>;
  });
}
