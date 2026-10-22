

export function formatRelativeTime(timestamp) {
  if (!timestamp) {
    return 'No activity yet';
  }
  const time = Date.parse(timestamp);
  if (!Number.isFinite(time)) {
    return timestamp;
  }
  const deltaMs = Date.now() - time;
  const absSeconds = Math.max(0, Math.round(deltaMs / 1000));
  if (absSeconds < 60) return 'Just now';
  if (absSeconds < 3600) return `${Math.round(absSeconds / 60)}m ago`;
  if (absSeconds < 86400) return `${Math.round(absSeconds / 3600)}h ago`;
  return `${Math.round(absSeconds / 86400)}d ago`;
}

// Status dot only carries colour when there's something worth surfacing —
// paused, running, or failed. A ready (or disabled) workflow leaves the rail
// empty, matching the Chat sidebar's session cards.
export function workflowDotTone(workflow, latestRun) {
  if (workflow?.paused) return 'paused';
  if (latestRun?.status === 'failed') return 'error';
  if (latestRun?.status === 'running') return 'info';
  return '';
}
