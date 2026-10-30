import { BROWSER_STORAGE_KEY } from '../../../lib/browserContracts';

// localStorage plumbing for the conversation page. All chat-local preferences
// flow through readJson/writeJsonOrRemove so the parse/validate/remove pattern
// lives in exactly one place.

export const ARCHIVED_CHAT_SESSIONS_KEY = BROWSER_STORAGE_KEY.ARCHIVED_CHAT_SESSIONS;
export const PINNED_CHAT_SESSIONS_KEY = BROWSER_STORAGE_KEY.PINNED_CHAT_SESSIONS;

export function readJson(key, fallback = null, warnLabel = null) {
  let saved;
  try {
    saved = localStorage.getItem(key);
  } catch (error) {
    if (warnLabel) {
      console.warn(warnLabel, error);
    }
    return fallback;
  }
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

export function normalizeQuickChatHandoff(value) {
  if (!value || typeof value !== 'object') {
    return null;
  }
  return {
    messages: Array.isArray(value.messages) ? value.messages : [],
    threadId: typeof value.threadId === 'string' ? value.threadId : null,
  };
}

export function loadQuickChatHandoff(storageKey) {
  return normalizeQuickChatHandoff(
    readJson(storageKey, null, 'Failed to load quick chat handoff:'),
  );
}
