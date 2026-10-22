export const MODULE_FILTERS = ['All', 'Available', 'Errors'];

const FILTER_STATES = { Available: ['available'], Errors: ['error', 'updated'] };

function entryMissing(module) {
  return Boolean(module?.entry) && !(module.files || []).includes(module.entry);
}

/**
 * Health of a module summary, checked in order:
 * - `error`: bad or missing manifest, missing entry file, or the current revision failed to load;
 * - `updated`: an older revision failed and the module has changed since (unconfirmed fix);
 * - `available`: everything else, including never opened (the quiet default).
 */
export function moduleState(module) {
  if (module?.manifest_error || entryMissing(module)) return 'error';
  if (module?.status?.state === 'error') {
    return module.status.revision === module.revision ? 'error' : 'updated';
  }
  return 'available';
}

export const MODULE_STATE_LABEL = { available: 'Available', updated: 'Updated', error: 'Error' };
export const MODULE_STATE_TONE = { available: 'idle', updated: 'info', error: 'error' };

/** Why a module is in `error`, one message per problem, for the alert under the header. */
export function moduleProblems(module) {
  if (!module) return [];
  const problems = [];
  if (module.manifest_error) problems.push(module.manifest_error);
  if (entryMissing(module)) problems.push(`Entry file ${module.entry} is missing.`);
  if (module.status?.state === 'error' && module.status.revision === module.revision) {
    problems.push(...(module.status.errors || []).map((error) => error.message).filter(Boolean));
  }
  return problems;
}

export function filterModules(modules, { filter = 'All', query = '' } = {}) {
  const wanted = FILTER_STATES[filter];
  const needle = query.trim().toLowerCase();
  return (modules || []).filter((module) => (
    (!wanted || wanted.includes(moduleState(module)))
    && (!needle || [module.name, module.title, module.description]
      .some((value) => String(value || '').toLowerCase().includes(needle)))
  ));
}

const LANGUAGES = { mjs: 'javascript', js: 'javascript', json: 'json', css: 'css', svg: 'xml', txt: 'plaintext' };

/** Markdown that renders one module file read-only: Markdown as-is, code fenced by type. */
export function moduleFileMarkdown(path, content) {
  const extension = String(path).split('.').pop().toLowerCase();
  if (extension === 'md') return content;
  const longestRun = Math.max(2, ...(String(content).match(/`+/g) || []).map((run) => run.length));
  const fence = '`'.repeat(longestRun + 1);
  return `${fence}${LANGUAGES[extension] || 'plaintext'}\n${content}\n${fence}`;
}

/** Gap that separates two editing sessions; the agent's edits within one request land seconds apart. */
export const REVISION_SESSION_GAP_MS = 2 * 60 * 1000;

/**
 * Collapse newest-first file revisions into editing sessions (bursts separated by
 * more than `gapMs`). Each session restores to its newest `captured_at`, and
 * `latestByPath` holds the state each touched file was left in.
 */
export function groupModuleRevisions(revisions, gapMs = REVISION_SESSION_GAP_MS) {
  const sessions = [];
  let current = null;
  let previousTime = null;
  for (const revision of revisions || []) {
    const time = Date.parse(revision.captured_at);
    if (!current || previousTime - time > gapMs) {
      current = { id: revision.id, capturedAt: revision.captured_at, revisions: [], latestByPath: {} };
      sessions.push(current);
    }
    previousTime = time;
    current.revisions.push(revision);
    current.startedAt = revision.captured_at;
    if (!(revision.path in current.latestByPath)) current.latestByPath[revision.path] = revision;
  }
  return sessions.map((session) => ({ ...session, paths: Object.keys(session.latestByPath).sort() }));
}
