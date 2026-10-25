import styles from './ContextWindowIndicator.module.css';

/** Provider-reported footprint of the latest model call, not cumulative usage. */
export default function ContextWindowIndicator({ usage }) {
  const { usedTokens, maxTokens } = usage || {};
  if (!Number.isSafeInteger(maxTokens) || maxTokens <= 0
    || !Number.isSafeInteger(usedTokens) || usedTokens < 0) return null;

  const fraction = Math.min(1, usedTokens / maxTokens);
  const percent = Math.round(fraction * 100);
  const format = new Intl.NumberFormat();
  const description = `${percent}% context used · ${format.format(usedTokens)} / ${format.format(maxTokens)} tokens (latest model call)`;

  return (
    <span
      className={styles.meter}
      role="meter"
      aria-label="Context window usage"
      aria-valuemin={0}
      aria-valuemax={maxTokens}
      aria-valuenow={Math.min(usedTokens, maxTokens)}
      aria-valuetext={description}
      title={description}
    >
      <span className={styles.pie} style={{ '--context-fill': `${fraction * 100}%` }} aria-hidden="true" />
    </span>
  );
}
