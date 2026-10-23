export function formatTokenCount(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 'Unknown';
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(number);
}

export function formatCost(amount, currency) {
  if (typeof amount !== 'string' && typeof amount !== 'number') return 'Unpriced';
  const numeric = Number(amount);
  if (!Number.isFinite(numeric) || !currency) return 'Unpriced';
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency', currency, maximumFractionDigits: 4,
    }).format(numeric);
  } catch {
    return `${amount} ${currency}`;
  }
}

export function formatBucketLabel(value, range) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Unknown time';
  const options = range === '24h'
    ? { hour: '2-digit', minute: '2-digit' }
    : { month: 'short', day: 'numeric' };
  return new Intl.DateTimeFormat(undefined, options).format(date);
}
