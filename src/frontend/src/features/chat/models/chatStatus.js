export const CHAT_STATUS = Object.freeze({
  IDLE: 'idle',
  DELEGATING: 'delegating',
  GENERATING: 'generating',
  COMPLETE: 'complete',
  ERROR: 'error',
});

/**
 * Derive one user-facing status from the runtime flags shared by chat views.
 * Delegating and generating win over terminal states so a retry cannot flash
 * stale completion or error state while a response is in flight.
 */
export function deriveChatStatus({
  delegating = false,
  generating = false,
  errored = false,
  unread = false,
} = {}) {
  if (delegating) return CHAT_STATUS.DELEGATING;
  if (generating) return CHAT_STATUS.GENERATING;
  if (errored) return CHAT_STATUS.ERROR;
  if (unread) return CHAT_STATUS.COMPLETE;
  return CHAT_STATUS.IDLE;
}
