import { useId } from 'react';
import styles from './ContextWindowIndicator.module.css';

/** Latest provider footprint; compaction savings compare observed input footprints. */
export default function ContextWindowIndicator({ usage }) {
  const tooltipId = useId();
  const { usedTokens, maxTokens } = usage || {};
  if (!Number.isSafeInteger(maxTokens) || maxTokens <= 0
    || !Number.isSafeInteger(usedTokens) || usedTokens < 0) return null;

  const fraction = Math.min(1, usedTokens / maxTokens);
  const percent = Math.round(fraction * 100);
  const format = new Intl.NumberFormat();
  const footprint = `${percent}% context used · ${format.format(usedTokens)} / ${format.format(maxTokens)} tokens (latest model call)`;
  const freed = usage?.compaction?.freedInputTokens;
  const description = Number.isSafeInteger(freed) && freed > 0
    ? `${footprint}. Last compaction: input decreased by ${format.format(freed)} tokens (${format.format(usage.compaction.beforeInputTokens)} → ${format.format(usage.compaction.afterInputTokens)}). Observed before/after calls; not exact tokenizer savings. Lifetime usage is unchanged.`
    : footprint;

  return (
    <span
      className={`chat-info-control ${styles.meter}`}
      tabIndex={0}
      role="meter"
      aria-label="Context window usage"
      aria-valuemin={0}
      aria-valuemax={maxTokens}
      aria-valuenow={Math.min(usedTokens, maxTokens)}
      aria-valuetext={description}
      aria-describedby={tooltipId}
    >
      <span className={styles.pie} style={{ '--context-fill': `${fraction * 100}%` }} aria-hidden="true" />
      <span id={tooltipId} className="chat-info-tooltip" role="tooltip">{description}</span>
    </span>
  );
}
