

export function formatTimestamp(timestamp) {
  if (!timestamp) return '';
  return new Date(timestamp * 1000).toLocaleString();
}

export function formatRelativeTimestamp(timestamp) {
  if (!timestamp) return 'now';
  const diffSeconds = Math.max(0, Math.floor(Date.now() / 1000 - timestamp));
  if (diffSeconds < 60) return 'now';
  const diffMinutes = Math.floor(diffSeconds / 60);
  if (diffMinutes < 60) return `${diffMinutes}m`;
  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours}h`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 7) return `${diffDays}d`;
  return new Date(timestamp * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' });
}
