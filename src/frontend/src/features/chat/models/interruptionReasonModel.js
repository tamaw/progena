const INTERRUPTION_REASONS = new Set(['user_cancelled']);

/** The backend/stored interruption reason, or null when absent or unknown. */
export function normalizeInterruptionReason(value) {
  return typeof value === 'string' && INTERRUPTION_REASONS.has(value) ? value : null;
}
