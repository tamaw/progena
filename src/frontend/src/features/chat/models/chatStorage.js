// localStorage plumbing for the conversation page. All chat-local preferences
// flow through readJson/writeJsonOrRemove so the parse/validate/remove pattern
// lives in exactly one place.

export const ARCHIVED_CHAT_SESSIONS_KEY = 'progena:archived-chat-sessions';
export const PINNED_CHAT_SESSIONS_KEY = 'progena:pinned-chat-sessions';

export function readJson(key, fallback = null, warnLabel = null) {
  const saved = localStorage.getItem(key);
  if (!saved) {
    return fallback;
  }
  try {
    return JSON.parse(saved);
  } catch (error) {
    if (warnLabel) {
      console.warn(warnLabel, error);
    }
    return fallback;
  }
}

export function writeJsonOrRemove(key, value, isEmpty) {
  if (isEmpty) {
    localStorage.removeItem(key);
    return;
  }
  localStorage.setItem(key, JSON.stringify(value));
}

function toStringIds(parsed) {
  return Array.isArray(parsed)
    ? parsed.filter((value) => typeof value === 'string' && value)
    : [];
}

export function loadStoredArchivedChatSessions() {
  const parsed = readJson(ARCHIVED_CHAT_SESSIONS_KEY, null, 'Failed to load archived chat sessions:');
  return Array.isArray(parsed) ? parsed : [];
}

export function loadStoredPinnedSessionIds() {
  return toStringIds(readJson(PINNED_CHAT_SESSIONS_KEY, null, 'Failed to load pinned chat sessions:'));
}

export function persistPinnedSessionIds(pinnedSessionIds) {
  writeJsonOrRemove(
    PINNED_CHAT_SESSIONS_KEY,
    pinnedSessionIds,
    !Array.isArray(pinnedSessionIds) || pinnedSessionIds.length === 0,
  );
}

export function loadQuickChatHandoff(storageKey) {
  const parsed = readJson(storageKey, null, 'Failed to load quick chat handoff:');
  if (!parsed || typeof parsed !== 'object') {
    return null;
  }
  return {
    messages: Array.isArray(parsed.messages) ? parsed.messages : [],
    threadId: typeof parsed.threadId === 'string' ? parsed.threadId : null,
  };
}
