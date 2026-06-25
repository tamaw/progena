export const STATUS_META = {
  draft: { label: 'Draft', tone: 'muted' },
  idle: { label: 'Idle', tone: 'muted' },
  running: { label: 'Running', tone: 'info' },
  complete: { label: 'Complete', tone: 'ok' },
  failed: { label: 'Failed', tone: 'error' },
  blocked: { label: 'Blocked', tone: 'error' },
};

export function formatTaskLabel(value) {
  return String(value || 'unassigned')
    .replace(/[_:]/g, ' ')
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

export function statusMeta(status) {
  return STATUS_META[status] || { label: formatTaskLabel(status || 'draft'), tone: 'muted' };
}
