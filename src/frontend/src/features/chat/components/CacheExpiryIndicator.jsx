import { useEffect, useState } from 'react';
import { cacheCountdownLabel } from '../models/contextWindowModel';
import ChatInfoTooltip from './ChatInfoTooltip';
import tooltipStyles from './ChatInfoTooltip.module.css';
import styles from './CacheExpiryIndicator.module.css';

function CacheCountdown({ expiresAt, ttlSeconds }) {
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
  const description = expired ? 'Cache window · elapsed (estimated)' : `Cache expiry · ~${label}`;
  return (
    <ChatInfoTooltip
      className={`${styles.indicator} ${expired ? styles.expired : ''}`}
      role="timer"
      aria-live="off"
      aria-label={description}
      content={(
        <>
          <span className={tooltipStyles.row}><span>Cache expiry · estimated</span><span>{expired ? 'Elapsed' : `~${label}`}</span></span>
          <span className={tooltipStyles.row}><span>Configured retention</span><span>{ttlSeconds / 60} min</span></span>
        </>
      )}
    >
      <span className={styles.time} aria-hidden="true">{label}</span>
    </ChatInfoTooltip>
  );
}

export default function CacheExpiryIndicator({ expiry }) {
  if (!Number.isSafeInteger(expiry?.expiresAt) || expiry.expiresAt <= 0
    || !Number.isSafeInteger(expiry?.ttlSeconds) || expiry.ttlSeconds <= 0) return null;
  return <CacheCountdown key={expiry.expiresAt} {...expiry} />;
}
