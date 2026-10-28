export function formatRelativeTime(timestamp) {
  const time = Date.parse(timestamp || '');
  if (!Number.isFinite(time)) return '—';
  const seconds = Math.max(0, Math.floor((Date.now() - time) / 1000));
  if (seconds < 10) return 'now';
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function formatDuration(start, end, now = Date.now()) {
  const startMs = Date.parse(start || '');
  if (!Number.isFinite(startMs)) return '—';
  const endMs = end ? Date.parse(end) : now;
  if (!Number.isFinite(endMs)) return '—';
  const milliseconds = Math.max(0, endMs - startMs);
  if (milliseconds < 1000) return `${Math.round(milliseconds)}ms`;
  if (milliseconds < 60000) return `${(milliseconds / 1000).toFixed(milliseconds < 10000 ? 1 : 0)}s`;
  const minutes = Math.floor(milliseconds / 60000);
  const seconds = Math.floor((milliseconds % 60000) / 1000);
  return `${minutes}m ${seconds}s`;
}

export function formatTokenCount(tokens) {
  if (tokens === null || tokens === undefined || tokens === '') return '—';
  const value = Number(tokens);
  if (!Number.isFinite(value)) return '—';
  return value >= 1000 ? `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}k` : String(value);
}

export function progressLabel(row) {
  const done = Number(row?.steps_done);
  const total = Number(row?.steps_total);
  if (!Number.isFinite(done) || !Number.isFinite(total) || total <= 0) return '—';
  return `${Math.min(done, total)}/${total}`;
}

export function progressPercent(row) {
  const done = Number(row?.steps_done);
  const total = Number(row?.steps_total);
  return Number.isFinite(done) && Number.isFinite(total) && total > 0
    ? Math.min(100, Math.max(0, (done / total) * 100))
    : null;
}

export function kindLabel(kind) {
  return ({ routine: 'Routine', workflow: 'Workflow', schedule: 'Schedule' })[kind] || kind || 'Run';
}
