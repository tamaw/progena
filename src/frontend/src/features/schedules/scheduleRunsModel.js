// Presentation of a schedule's recent runs (see the backend schedule snapshot's
// per-schedule `runs`). `interrupted` is derived by the backend from a gone
// runtime owner; it is never stored.

export function scheduleRunStatusTone(status) {
  switch (String(status || '').toLowerCase()) {
    case 'completed':
      return 'ok';
    case 'error':
      return 'error';
    case 'interrupted':
      return 'warning';
    default:
      return 'muted';
  }
}

function runDuration(startedAt, completedAt) {
  const started = Date.parse(startedAt);
  const completed = Date.parse(completedAt);
  if (!Number.isFinite(started) || !Number.isFinite(completed)) return null;
  const seconds = Math.max(0, Math.round((completed - started) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return remainingSeconds ? `${minutes}m ${remainingSeconds}s` : `${minutes}m`;
}

export function scheduleRunDetail(run) {
  const duration = runDuration(run?.started_at, run?.completed_at);
  if (duration) return `${duration} duration`;
  if (run?.error) return run.error;
  const status = String(run?.status || '').toLowerCase();
  if (status === 'interrupted') return 'The runtime restarted during this run';
  if (status === 'running') return 'In progress';
  if (status === 'queued') return 'Queued';
  return '';
}
