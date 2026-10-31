export const CANVAS_FILTERS = ['All', 'Available', 'Errors'];

const FILTER_STATES = { Available: ['available'], Errors: ['error', 'updated'] };

function entryMissing(canvas) {
  return Boolean(canvas?.entry) && !(canvas.files || []).includes(canvas.entry);
}

/**
 * Health of a canvas summary, checked in order:
 * - `error`: bad or missing manifest, missing entry file, or the current revision failed to load;
 * - `updated`: an older revision failed and the canvas has changed since (unconfirmed fix);
 * - `available`: everything else, including never opened (the quiet default).
 */
export function canvasState(canvas) {
  if (canvas?.manifest_error || entryMissing(canvas)) return 'error';
  if (canvas?.status?.state === 'error') {
    return canvas.status.revision === canvas.revision ? 'error' : 'updated';
  }
  return 'available';
}

export const CANVAS_STATE_LABEL = { available: 'Available', updated: 'Updated', error: 'Error' };
// Updated: the canvas changed since its last reported error -- new data, green.
export const CANVAS_STATE_TONE = { available: 'idle', updated: 'ok', error: 'error' };

/** Why a canvas is in `error`, one message per problem, for the alert under the header. */
export function canvasProblems(canvas) {
  if (!canvas) return [];
  const problems = [];
  if (canvas.manifest_error) problems.push(canvas.manifest_error);
  if (entryMissing(canvas)) problems.push(`Entry file ${canvas.entry} is missing.`);
  if (canvas.status?.state === 'error' && canvas.status.revision === canvas.revision) {
    problems.push(...(canvas.status.errors || []).map((error) => error.message).filter(Boolean));
  }
  return problems;
}

export function filterCanvases(canvases, { filter = 'All', query = '' } = {}) {
  const wanted = FILTER_STATES[filter];
  const needle = query.trim().toLowerCase();
  return (canvases || []).filter((canvas) => (
    (!wanted || wanted.includes(canvasState(canvas)))
    && (!needle || [canvas.name, canvas.title, canvas.description]
      .some((value) => String(value || '').toLowerCase().includes(needle)))
  ));
}
