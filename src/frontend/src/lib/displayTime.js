/** Return a valid Date for a date-like input, otherwise null. */
export function parseDateOrNull(value) {
  if (value === null || value === undefined || value === '') {
    return null;
  }
  try {
    const date = value instanceof Date ? new Date(value.getTime()) : new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  } catch {
    return null;
  }
}

/**
 * Compact age for lists: now, minutes, hours, days, then a local short date.
 * `now` is injectable to make the user-visible boundaries deterministic.
 */
export function formatCompactRelativeAge(value, now = Date.now()) {
  const date = parseDateOrNull(value);
  if (!date) {
    return '--';
  }

  const reference = parseDateOrNull(now);
  const diff = Math.max(0, (reference?.getTime() ?? Date.now()) - date.getTime());
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'now';
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

/** Format a valid date-like value in the concise local date-and-time style. */
export function formatShortLocalDateTime(value) {
  const date = parseDateOrNull(value);
  if (!date) {
    return '';
  }
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}
