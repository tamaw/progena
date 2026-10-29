
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

export function kindLabel(kind) {
  return ({ routine: 'Routine', workflow: 'Workflow', schedule: 'Schedule' })[kind] || kind || 'Run';
}
