import { useId } from 'react';
import styles from './MessageTokenUsage.module.css';

function formatOutputTokens(value) {
  return new Intl.NumberFormat().format(value);
}

/** Provider-reported answer output with an optional final-call breakdown. */
export default function MessageTokenUsage({ tokenCount, finalCallTokenCount }) {
  const tooltipId = useId();
  const hasRootCount = Number.isSafeInteger(tokenCount) && tokenCount >= 0;
  const hasFinalCallCount = Number.isSafeInteger(finalCallTokenCount) && finalCallTokenCount >= 0;
  if (!hasRootCount && !hasFinalCallCount) return null;
  const rootLabel = hasRootCount ? `${formatOutputTokens(tokenCount)} output tokens` : 'Unavailable';
  const finalCallLabel = hasFinalCallCount
    ? `${formatOutputTokens(finalCallTokenCount)} output tokens`
    : 'Unavailable';
  const intermediateLabel = hasRootCount && hasFinalCallCount && finalCallTokenCount <= tokenCount
    ? `${formatOutputTokens(tokenCount - finalCallTokenCount)} output tokens`
    : 'Unavailable';

  return (
    <span
      className={styles.usage}
      tabIndex={0}
      aria-label={`${rootLabel}; whole response total. Final model call: ${finalCallLabel}.`}
      aria-describedby={tooltipId}
    >
      {hasRootCount ? rootLabel : `Final call: ${finalCallLabel}`}
      <span id={tooltipId} className={styles.tooltip} role="tooltip">
        <span className={styles.tooltipTitle}>Output-token breakdown</span>
        <span className={styles.tooltipRow}>
          <span>Whole response</span>
          <span>{rootLabel}</span>
        </span>
        <span className={styles.tooltipDetail}>Root-agent output only, including reasoning. Excludes input and delegated-agent usage.</span>
        <span className={styles.tooltipRow}>
          <span>Intermediate model calls</span>
          <span>{intermediateLabel}</span>
        </span>
        <span className={styles.tooltipRow}>
          <span>Final model call</span>
          <span>{finalCallLabel}</span>
        </span>
      </span>
    </span>
  );
}
