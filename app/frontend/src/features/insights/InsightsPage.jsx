import { useEffect, useMemo, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import InlineFailure from '../../components/InlineFailure';
import Select from '../../components/Select';
import { INSIGHT_RANGES } from './state';
import { fetchModelProfiles, selectModelProfiles, selectModelProfilesState } from '../settings/state/modelProfiles';
import { useConfig } from '../../context/ConfigContext';
import { selectRealtimeStreamReady } from '../../realtime/realtimeSlice';
import { useInsightsQuery } from './useInsightsQuery';
import { formatBucketLabel, formatTokenCount } from './usageFormat';
import CostAmounts from './CostAmounts';

function MetricCard({ label, children, detail }) {
  return <section className="insights-metric"><span>{label}</span><strong>{children}</strong>{detail ? <small>{detail}</small> : null}</section>;
}

function CoverageNotice({ coverage, pricingBasis, unknownUsageCount }) {
  const warnings = Array.isArray(coverage?.warnings) ? coverage.warnings : [];
  return (
    <aside className="insights-coverage" aria-live="polite">
      <strong>{pricingBasis === 'current_profile_rates' ? 'Current rates' : 'Rates unavailable'}</strong>
      {coverage?.unattributed_call_count ? <span>{coverage.unattributed_call_count} unattributed</span> : null}
      {unknownUsageCount ? <span>{unknownUsageCount} incomplete</span> : null}
      {warnings.map((warning) => <span key={warning}>{warning}</span>)}
    </aside>
  );
}

function UsageTrend({ series, range }) {
  const rows = Array.isArray(series) ? series : [];
  const max = Math.max(0, ...rows.map((row) => Number(row.total_tokens) || 0));
  if (!rows.length) return <div className="insights-empty">No usage</div>;
  return (
    <div className="insights-trend" aria-label="Token usage trend">
      {rows.map((row) => {
        const total = Number(row.total_tokens) || 0;
        return <div className="insights-trend__bucket" key={row.bucket_start} title={`${new Date(row.bucket_start).toLocaleString()}: ${formatTokenCount(total)} tokens`}>
          <span className="insights-trend__bar" style={{ height: `${max && total ? Math.max(3, (total / max) * 100) : 0}%` }} />
          <small>{formatBucketLabel(row.bucket_start, range)}</small>
        </div>;
      })}
    </div>
  );
}

function UsageRankings({ rows }) {
  if (!rows.length) return <div className="insights-empty">No usage</div>;
  return <div className="insights-ranking-list">{rows.map((row, index) => <div className="insights-ranking" key={`${row.profile_name ?? 'unattributed'}:${row.deployment ?? index}`}>
    <div><strong>{row.profile_name || 'Unattributed'}</strong><small>{row.deployment || 'Deployment unavailable'}</small></div>
    <div><strong>{formatTokenCount(row.total_tokens)}</strong><small>{row.call_count || 0} calls · <CostAmounts metrics={row} /></small></div>
  </div>)}</div>;
}

export default function InsightsPage() {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const streamReady = useSelector(selectRealtimeStreamReady);
  const profiles = useSelector(selectModelProfiles);
  const profilesState = useSelector(selectModelProfilesState);
  const [range, setRange] = useState('7d');
  const [profile, setProfile] = useState(null);
  const { snapshot, loading, error, refresh, streamReady: queryReady } = useInsightsQuery({ range, profile });

  useEffect(() => {
    if (streamReady && !profilesState.loaded && !profilesState.loading && !profilesState.error) {
      dispatch(fetchModelProfiles({ modelProfilesUrl: getApiUrl('modelProfiles') }));
    }
  }, [dispatch, getApiUrl, profilesState.loaded, profilesState.loading, profilesState.error, streamReady]);

  const profileNames = useMemo(() => [...new Set([
    ...profiles.map((item) => item?.name),
    ...(snapshot?.by_profile || []).map((item) => item.profile_name),
    profile,
  ].filter(Boolean))].sort(), [profile, profiles, snapshot]);
  const totals = snapshot?.totals || {};
  const rows = useMemo(() => (
    Array.isArray(snapshot?.by_profile)
      ? [...snapshot.by_profile].sort((left, right) => right.total_tokens - left.total_tokens)
      : []
  ), [snapshot]);
  const hasActivity = Boolean(totals?.call_count || totals?.unknown_usage_count || totals?.total_tokens);

  return <div className="insights-page">
    <header className="insights-page__header">
      <div><p className="insights-page__eyebrow">Spectate</p><h1>Insights</h1></div>
      <div className="insights-controls">
        <label>Range<Select value={range} onChange={setRange} options={INSIGHT_RANGES.map((value) => ({ value, label: value }))} ariaLabel="Range" className="insights-range" /></label>
        <label>Profile<Select value={profile || ''} onChange={(value) => setProfile(value || null)} options={[{ value: '', label: 'All profiles' }, ...profileNames.map((name) => ({ value: name, label: name }))]} ariaLabel="Profile" searchable className="insights-profile" /></label>
        <button type="button" className="task-btn task-btn--secondary" onClick={refresh} disabled={loading}>Refresh</button>
      </div>
    </header>
    {error ? <InlineFailure failure={error} operation="Load Insights" onRetry={refresh} /> : null}
    {!queryReady ? <div className="insights-empty">Connecting…</div> : null}
    {loading && !snapshot ? <div className="insights-empty">Loading…</div> : null}
    {snapshot ? <>
      <div className="insights-metrics">
        <MetricCard label="Total tokens" detail={`${totals.call_count || 0} model calls`}>{formatTokenCount(totals.total_tokens)}</MetricCard>
        <MetricCard label="Input tokens" detail={totals.cache_read_tokens && totals.input_tokens ? `${formatTokenCount(totals.cache_read_tokens)} cached (${Math.round((totals.cache_read_tokens / totals.input_tokens) * 100)}%)` : null}>{formatTokenCount(totals.input_tokens)}</MetricCard>
        <MetricCard label="Output tokens" detail={totals.reasoning_tokens ? `${formatTokenCount(totals.reasoning_tokens)} reasoning` : null}>{formatTokenCount(totals.output_tokens)}</MetricCard>
        <MetricCard label="Estimated cost" detail={totals.unpriced_call_count ? `${totals.unpriced_call_count} unpriced call${totals.unpriced_call_count === 1 ? '' : 's'}` : null}><CostAmounts metrics={totals} /></MetricCard>
      </div>
      {!hasActivity ? <div className="insights-empty">No activity</div> : null}
      <div className="insights-grid">
        <section className="insights-card insights-card--trend"><header><h2>Usage trend</h2></header><UsageTrend series={snapshot.series} range={range} /></section>
        <section className="insights-card"><header><h2>Profiles & deployments</h2></header><UsageRankings rows={rows} /></section>
      </div>
      <CoverageNotice coverage={snapshot.coverage} pricingBasis={snapshot.pricing_basis} unknownUsageCount={totals.unknown_usage_count} />
    </> : null}
  </div>;
}
