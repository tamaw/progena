import { formatCost } from './usageFormat';

export default function CostAmounts({ metrics, className = '' }) {
  const costs = Array.isArray(metrics?.costs)
    ? metrics.costs.filter((cost) => cost?.currency && cost.amount != null)
    : [];
  if (costs.length === 0) return <span className={className}>Unpriced</span>;
  return (
    <span className={className}>
      {costs.map((cost) => <span key={cost.currency}>{formatCost(cost.amount, cost.currency)}</span>)}
    </span>
  );
}
