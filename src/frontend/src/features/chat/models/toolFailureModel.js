/**
 * Whether a lifecycle event represents a failed tool invocation. This stays
 * independent of rendering so filters, summaries, and tool output agree.
 */
const FAILURE_STATES = new Set(['error', 'failed', 'failure', 'rejected']);
// Fallback for events without server classification; mirrors
// `tool_result_failed` in src/progena/sessions/messages.py.
const EXECUTE_FAILURE = /^\[Command failed with exit code -?\d+\]$/m;
const JAVASCRIPT_ERROR = /^<error type="/m;

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
    || renderedResultIsFailure(metadata);
}
