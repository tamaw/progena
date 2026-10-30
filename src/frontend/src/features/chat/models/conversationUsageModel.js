const COVERAGE = new Set(['complete', 'partial', 'unknown']);

/** Authoritative session totals, independent of the visible transcript page. */
export function conversationUsageDisplay(usage) {
  const format = new Intl.NumberFormat();
  const metric = (name, label) => {
    const count = usage?.[`${name}_tokens`];
    const coverage = usage?.[`${name}_coverage`];
    if (!Number.isSafeInteger(count) || count < 0 || !COVERAGE.has(coverage) || coverage === 'unknown') {
      return name === 'reasoning' ? '0 reasoning tokens' : `${label} unavailable`;
    }
    return `${coverage === 'partial' ? '≥' : ''}${format.format(count)} ${coverage === 'partial' ? 'recorded ' : ''}${label}`;
  };
  return {
    output: metric('output', 'output tokens'),
    reasoning: metric('reasoning', 'reasoning tokens'),
  };
}
