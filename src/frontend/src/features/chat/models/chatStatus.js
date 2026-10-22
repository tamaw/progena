export const CHAT_STATUS = Object.freeze({
  IDLE: 'idle',
  GENERATING: 'generating',
  COMPLETE: 'complete',
  ERROR: 'error',
});

/**
 * Derive one user-facing status from the runtime flags shared by chat views.
 * Generating wins over terminal states so a retry cannot flash stale completion or
 * error state while a new response is in flight.
 */
export function deriveChatStatus({ generating = false, errored = false, unread = false } = {}) {
  if (generating) return CHAT_STATUS.GENERATING;
  if (errored) return CHAT_STATUS.ERROR;
  if (unread) return CHAT_STATUS.COMPLETE;
  return CHAT_STATUS.IDLE;
}
