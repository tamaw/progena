// localStorage plumbing for the conversation page. All chat-local preferences
// flow through readJson/writeJsonOrRemove so the parse/validate/remove pattern
// lives in exactly one place.

export const ARCHIVED_CHAT_SESSIONS_KEY = 'progenitor:archived-chat-sessions';
export const CUSTOM_CHAT_TITLE_KEY = 'progenitor:chat-custom-title';
export const PINNED_CHAT_SESSIONS_KEY = 'progenitor:pinned-chat-sessions';
export const SHARED_CHAT_SESSIONS_KEY = 'progenitor:shared-chat-sessions';

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

export function loadStoredSharedSessionIds() {
  return toStringIds(readJson(SHARED_CHAT_SESSIONS_KEY, null, 'Failed to load shared chat sessions:'));
}

export function persistSharedSessionIds(sharedSessionIds) {
  writeJsonOrRemove(
    SHARED_CHAT_SESSIONS_KEY,
    sharedSessionIds,
    !Array.isArray(sharedSessionIds) || sharedSessionIds.length === 0,
  );
}

export function loadStoredCustomChatTitle() {
  const parsed = readJson(CUSTOM_CHAT_TITLE_KEY, null, 'Failed to load custom chat title:');
  if (!parsed || typeof parsed !== 'object') {
    return { sessionId: null, title: '' };
  }
  return {
    sessionId: typeof parsed.sessionId === 'string' ? parsed.sessionId : null,
    title: typeof parsed.title === 'string' ? parsed.title : '',
  };
}

export function persistCustomChatTitle(sessionId, title) {
  const normalizedTitle = typeof title === 'string' ? title.trim() : '';
  writeJsonOrRemove(
    CUSTOM_CHAT_TITLE_KEY,
    { sessionId: typeof sessionId === 'string' ? sessionId : null, title: normalizedTitle },
    !normalizedTitle,
  );
}

export function loadQuickChatHandoff(storageKey) {
  const parsed = readJson(storageKey, null, 'Failed to load quick chat handoff:');
  if (!parsed || typeof parsed !== 'object') {
    return null;
  }
  return {
    messages: Array.isArray(parsed.messages) ? parsed.messages : [],
    sessionId: typeof parsed.sessionId === 'string' ? parsed.sessionId : null,
  };
}
