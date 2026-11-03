/**
 * Whether a lifecycle event represents a failed tool invocation. This stays
 * independent of rendering so filters, summaries, and tool output agree.
 */
const FAILURE_STATES = new Set(['error', 'failed', 'failure', 'rejected']);
// Fallback for events without server classification; mirrors
// `tool_result_failed` in src/progena/sessions/messages.py.
const EXECUTE_FAILURE = /^\[Command failed with exit code -?\d+\]$/m;
const JAVASCRIPT_ERROR = /^<error type="/m;

/**
 * Routine task failures return normally from the enclosing `task` invocation
 * so the agent can decide whether to retry. Match only the routine wrapper's
 * exact result protocol and its known delegated name; normal agent prose
 * mentioning a routine failure is still successful output.
 */
export function isRoutineResultFailure(resultPreview, routineName) {
  if (
    typeof resultPreview !== 'string'
    || typeof routineName !== 'string'
    || !routineName
  ) return false;
  const prefix = `Routine '${routineName}' `;
  return resultPreview.startsWith(`${prefix}failed:`) || resultPreview.startsWith(`${prefix}input error:`);
}

function routineResultIsFailure(metadata) {
  return isRoutineResultFailure(
    metadata?.result_preview,
    metadata?.subagent_name ?? metadata?.subagent_type,
  );
}


function isFailureState(value) {
  return typeof value === 'string' && FAILURE_STATES.has(value.toLowerCase());
}

function renderedResultIsFailure(metadata) {
  if (typeof metadata?.result_preview !== 'string') return false;
  if (metadata.tool_name === 'execute') return EXECUTE_FAILURE.test(metadata.result_preview);
  if (metadata.tool_name === 'javascript') return JAVASCRIPT_ERROR.test(metadata.result_preview);
  return false;
}

/**
 * Matches explicit lifecycle failures, transport failure metadata, and the
 * result formats that the tool renderer presents as errors.
 */
export function isToolEventFailure(event) {
  if (!event || typeof event !== 'object') return false;
  if (typeof event.eventType === 'string' && event.eventType.endsWith('_error')) return true;

  const metadata = event.metadata;
  if (!metadata || typeof metadata !== 'object') return false;
  return Boolean(metadata.error)
    || metadata.success === false
    || isFailureState(metadata.status)
    || isFailureState(metadata.state)
    || renderedResultIsFailure(metadata)
    || routineResultIsFailure(metadata);
}
