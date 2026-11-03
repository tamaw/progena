import tooltipStyles from './ChatInfoTooltip.module.css';
import TokenCountIcon from './TokenCountIcon';

function formatOutputTokens(value) {
  return new Intl.NumberFormat().format(value);
}

/** Provider-reported answer output with an optional final-call breakdown. */
export default function MessageTokenUsage({ tokenCount, finalCallTokenCount }) {
  const hasRootCount = Number.isSafeInteger(tokenCount) && tokenCount >= 0;
  const hasFinalCallCount = Number.isSafeInteger(finalCallTokenCount) && finalCallTokenCount >= 0;
  if (!hasRootCount && !hasFinalCallCount) return null;
  const rootLabel = hasRootCount ? `${formatOutputTokens(tokenCount)} output tokens` : 'Unknown';
  const finalCallLabel = hasFinalCallCount ? formatOutputTokens(finalCallTokenCount) : 'Unknown';
  const intermediateLabel = hasRootCount && hasFinalCallCount && finalCallTokenCount <= tokenCount
    ? formatOutputTokens(tokenCount - finalCallTokenCount)
    : 'Unknown';

  return (
    <TokenCountIcon
      label={hasRootCount ? rootLabel : `Final call: ${finalCallLabel} output tokens`}
      details={(
        <>
          <span className={tooltipStyles.row}><span>Output</span><span>{hasRootCount ? formatOutputTokens(tokenCount) : 'Unknown'}</span></span>
          <span className={tooltipStyles.row}><span>Intermediate</span><span>{intermediateLabel}</span></span>
          <span className={tooltipStyles.row}><span>Final call</span><span>{finalCallLabel}</span></span>
        </>
      )}
    />
  );
}
