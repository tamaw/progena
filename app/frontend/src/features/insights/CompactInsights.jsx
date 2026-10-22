import { useState } from 'react';
import { Link } from 'react-router-dom';
import Select from '../../components/Select';
import { INSIGHT_RANGES } from './state';
import { useInsightsQuery } from './useInsightsQuery';
import { formatTokenCount } from './usageFormat';
import CostAmounts from './CostAmounts';

export default function CompactInsights({ onNavigate }) {
  const [range, setRange] = useState('7d');
  const { snapshot, loading, error, refresh, streamReady } = useInsightsQuery({ range });
  const totals = snapshot?.totals || {};
  const coverage = snapshot?.coverage;
  const noActivity = snapshot && !totals?.call_count && !totals?.unknown_usage_count && !totals?.total_tokens;

  return <div className="compact-insights">
    <div className="compact-insights__toolbar">
      <label>Range<Select value={range} onChange={setRange} options={INSIGHT_RANGES.map((value) => ({ value, label: value }))} ariaLabel="Range" /></label>
      <button type="button" className="task-btn task-btn--secondary" onClick={refresh} disabled={loading}>Refresh</button>
    </div>
    {error ? <div className="compact-insights__error">Usage unavailable <button type="button" className="task-btn task-btn--secondary" onClick={refresh}>Retry</button></div> : null}
    {!streamReady ? <div className="compact-insights__empty">Connecting…</div> : null}
    {loading && !snapshot ? <div className="compact-insights__empty">Loading…</div> : null}
    {snapshot ? <>
      <div className="compact-insights__metrics">
        <div><span>Tokens</span><strong>{formatTokenCount(totals.total_tokens)}</strong><small>{totals.call_count || 0} calls</small></div>
        <div><span>Estimated cost</span><strong><CostAmounts metrics={totals} /></strong><small>{totals.unpriced_call_count ? `${totals.unpriced_call_count} unpriced` : 'Current rates'}</small></div>
      </div>
      {noActivity ? <div className="compact-insights__empty">No activity</div> : null}
      {coverage?.unattributed_call_count ? <div className="compact-insights__notice">{coverage.unattributed_call_count} unattributed</div> : null}
      {Array.isArray(coverage?.warnings) && coverage.warnings.map((warning) => <div className="compact-insights__notice" key={warning}>{warning}</div>)}
    </> : null}
    <Link to="/insights" onClick={onNavigate} className="compact-insights__link">Open full Insights</Link>
  </div>;
}
