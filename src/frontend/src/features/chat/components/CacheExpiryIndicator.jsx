import { useEffect, useId, useState } from 'react';
import { cacheCountdownLabel } from '../models/contextWindowModel';
import styles from './CacheExpiryIndicator.module.css';

function CacheCountdown({ expiresAt, ttlSeconds }) {
  const tooltipId = useId();
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (now >= expiresAt) return;
    const delay = (expiresAt - now) % 60000 || 60000;
    const timer = setTimeout(() => setNow(Date.now()), delay);
    return () => clearTimeout(timer);
  }, [expiresAt, now]);
  useEffect(() => {
    const refresh = () => {
      if (!document.hidden) setNow(Date.now());
    };
    document.addEventListener('visibilitychange', refresh);
    return () => document.removeEventListener('visibilitychange', refresh);
  }, []);

  const expired = now >= expiresAt;
  const label = cacheCountdownLabel(expiresAt, now);
  const description = expired
    ? 'Estimated prompt-cache window elapsed. Provider cache state is not observable.'
    : `Estimated prompt-cache expiry in ${label}, based on the last response and a configured ${ttlSeconds / 60}-minute retention. Provider cache state may differ.`;
  return (
    <span
      className={`chat-info-control ${styles.indicator} ${expired ? styles.expired : ''}`}
      tabIndex={0}
      role="timer"
      aria-live="off"
      aria-label="Estimated prompt-cache expiry"
      aria-describedby={tooltipId}
    >
      <span className={styles.time} aria-hidden="true">{label}</span>
      <span id={tooltipId} className="chat-info-tooltip" role="tooltip">{description}</span>
    </span>
  );
}

export default function CacheExpiryIndicator({ expiry }) {
  if (!Number.isSafeInteger(expiry?.expiresAt) || expiry.expiresAt <= 0
    || !Number.isSafeInteger(expiry?.ttlSeconds) || expiry.ttlSeconds <= 0) return null;
  return <CacheCountdown key={expiry.expiresAt} {...expiry} />;
}
