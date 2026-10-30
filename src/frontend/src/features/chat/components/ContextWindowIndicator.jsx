import ChatInfoTooltip from './ChatInfoTooltip';
import tooltipStyles from './ChatInfoTooltip.module.css';
import styles from './ContextWindowIndicator.module.css';

/** Latest provider footprint; compaction savings compare observed input footprints. */
export default function ContextWindowIndicator({ usage }) {
  const { usedTokens, maxTokens } = usage || {};
  if (!Number.isSafeInteger(maxTokens) || maxTokens <= 0
    || !Number.isSafeInteger(usedTokens) || usedTokens < 0) return null;

  const fraction = Math.min(1, usedTokens / maxTokens);
  const percent = Math.round(fraction * 100);
  const format = new Intl.NumberFormat();
  const footprint = `${percent}% context · ${format.format(usedTokens)} / ${format.format(maxTokens)} tokens`;
  const freed = usage?.compaction?.freedInputTokens;
  const hasReduction = Number.isSafeInteger(freed) && freed > 0;
  const description = hasReduction ? `${footprint}; input reduction ${format.format(freed)} tokens (observed)` : footprint;

  return (
    <ChatInfoTooltip
      className={styles.meter}
      role="meter"
      aria-label="Context window usage"
      aria-valuemin={0}
      aria-valuemax={maxTokens}
      aria-valuenow={Math.min(usedTokens, maxTokens)}
      aria-valuetext={description}
      content={(
        <>
          <span className={tooltipStyles.row}><span>Context · {percent}%</span><span>{format.format(usedTokens)} / {format.format(maxTokens)}</span></span>
          {hasReduction && (
            <>
              <span className={tooltipStyles.row}><span>Input reduction · observed</span><span>{format.format(freed)}</span></span>
              <span className={tooltipStyles.row}><span>Before → after</span><span>{format.format(usage.compaction.beforeInputTokens)} → {format.format(usage.compaction.afterInputTokens)}</span></span>
            </>
          )}
        </>
      )}
    >
      <span className={styles.pie} style={{ '--context-fill': `${fraction * 100}%` }} aria-hidden="true" />
    </ChatInfoTooltip>
  );
}
