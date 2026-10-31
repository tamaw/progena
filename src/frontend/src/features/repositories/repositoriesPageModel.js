// Pure helpers for the Repositories page, kept free of React so the status
// rules are unit-testable.

// `pending` is a missing scan or checkout ("Not yet cloned"), not a scan in
// progress, so it is amber rather than a working colour.
const REPO_STATUS = {
  clean: { label: 'Synced', tone: 'idle' },
  upstream: { label: 'Changes upstream', tone: 'ok' },
  dirty: { label: 'Uncommitted changes', tone: 'warning' },
  error: { label: 'Scan error', tone: 'error' },
  pending: { label: 'Not yet cloned', tone: 'warning' },
};

function scanStatusKey(scan) {
  if (!scan || !scan.exists) return 'pending';
  if (!scan.is_git_repo || scan.error) return 'error';
  if ((scan.dirty_count || 0) > 0) return 'dirty';
  if ((scan.upstream_ahead || 0) > 0 || (scan.upstream_behind || 0) > 0) return 'upstream';
  return 'clean';
}

/**
 * List-dot and header status for a repository, derived entirely from its
 * latest heartbeat scan (there is no PR/CI signal). Precedence: missing scan
 * or checkout > scan error > uncommitted changes > upstream changes > synced.
 */
export function repoStatus(scan) {
  return REPO_STATUS[scanStatusKey(scan)];
}
