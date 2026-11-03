// "Open in Quick Chat" from the Chat page: the panel continues the same
// canonical conversation (same thread_id) rather than cloning it or replaying
// its transcript as a prompt. The shell validates the requested thread and
// stamps each request with a unique id so the panel adopts it exactly once,
// even across panel remounts.

const MAX_THREAD_ID_LENGTH = 200;
const THREAD_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]*$/;

let nextTargetId = 0;

export function normalizeQuickChatThreadId(value) {
  if (typeof value !== 'string') return null;
  const threadId = value.trim();
  if (!threadId || threadId.length > MAX_THREAD_ID_LENGTH || !THREAD_ID_PATTERN.test(threadId)) {
    return null;
  }
  return threadId;
}

/** Returns `{ id, threadId }` for a valid `{ threadId }` request, else null. */
export function createQuickChatThreadTarget(detail) {
  const threadId = normalizeQuickChatThreadId(detail?.threadId);
  if (!threadId) return null;
  nextTargetId += 1;
  return { id: `quick-chat-target-${nextTargetId}`, threadId };
}

/**
 * Decides how the panel handles a requested thread:
 * - `ignore`: no target, or this target was already adopted.
 * - `busy`: the panel's own reply is still streaming on another thread; it
 *   is never cancelled or orphaned by an adoption.
 * - `refresh`: the target is already the panel's thread.
 * - `adopt`: switch the panel to the target thread.
 */
export function resolveQuickChatThreadAdoption({ target, adoptedTargetId, currentThreadId, ownStreamActive }) {
  if (!target?.threadId || target.id === adoptedTargetId) return 'ignore';
  if (target.threadId === currentThreadId) return 'refresh';
  if (ownStreamActive) return 'busy';
  return 'adopt';
}
