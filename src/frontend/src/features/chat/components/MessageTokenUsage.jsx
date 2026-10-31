import { useState } from 'react';
import ChatInfoTooltip from './ChatInfoTooltip';
import tooltipStyles from './ChatInfoTooltip.module.css';
import styles from './MessageTokenUsage.module.css';

function TokenIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" aria-hidden="true">
      <path d="M3 6h10.5M2.5 10H13M6.5 2.5l-1 11M10.5 2.5l-1 11" />
    </svg>
  );
}

function formatOutputTokens(value) {
  return new Intl.NumberFormat().format(value);
}

/**
 * Provider-reported answer output with an optional final-call breakdown. Like
 * Copy/Regenerate, an icon widens to reveal the count on hover or focus;
 * click, tap, Enter or Space pins it open until Escape or blur.
 */
export default function MessageTokenUsage({ tokenCount, finalCallTokenCount }) {
  const [expanded, setExpanded] = useState(false);
  const hasRootCount = Number.isSafeInteger(tokenCount) && tokenCount >= 0;
  const hasFinalCallCount = Number.isSafeInteger(finalCallTokenCount) && finalCallTokenCount >= 0;
  if (!hasRootCount && !hasFinalCallCount) return null;
  const rootLabel = hasRootCount ? `${formatOutputTokens(tokenCount)} output tokens` : 'Unknown';
  const finalCallLabel = hasFinalCallCount ? formatOutputTokens(finalCallTokenCount) : 'Unknown';
  const intermediateLabel = hasRootCount && hasFinalCallCount && finalCallTokenCount <= tokenCount
    ? formatOutputTokens(tokenCount - finalCallTokenCount)
    : 'Unknown';

  const label = hasRootCount ? rootLabel : `Final call: ${finalCallLabel} output tokens`;

  return (
    <ChatInfoTooltip
      className={`${styles.usage}${expanded ? ` ${styles.expanded}` : ''}`}
      role="button"
      aria-label={label}
      aria-expanded={expanded}
      onClick={() => setExpanded((value) => !value)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          setExpanded((value) => !value);
        } else if (event.key === 'Escape') {
          setExpanded(false);
        }
      }}
      onBlur={() => setExpanded(false)}
      content={(
        <>
          <span className={tooltipStyles.row}><span>Output</span><span>{hasRootCount ? formatOutputTokens(tokenCount) : 'Unknown'}</span></span>
          <span className={tooltipStyles.row}><span>Intermediate</span><span>{intermediateLabel}</span></span>
          <span className={tooltipStyles.row}><span>Final call</span><span>{finalCallLabel}</span></span>
        </>
      )}
    >
      <TokenIcon />
      <span className={styles.label}>{label}</span>
    </ChatInfoTooltip>
  );
}
