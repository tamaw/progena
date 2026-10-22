import { useCallback, useEffect, useState } from 'react';
import { reportFailure } from '../../../lib/failures';
import {
  loadStoredArchivedChatSessions,
  loadStoredPinnedSessionIds,
  persistPinnedSessionIds,
} from '../models/chatStorage';
import { updateChatSessionMetadata } from '../state/chats';

// Prune persisted selection ids whose chats no longer exist. Drives the
// pinned id set. Called from the page after `sessionItems` is derived.

// Prune persisted selection ids whose chats no longer exist. Drives the
// pinned id set. Called from the page after `sessionItems` is derived.
export function usePrunedSelection({ ids, setIds, persist, sessionItems, show, operation }) {
  useEffect(() => {
    const activeKeys = new Set(sessionItems.map((item) => item.pinKey));
    if (!ids.some((key) => !activeKeys.has(key))) {
      return;
    }

    const nextIds = ids.filter((key) => activeKeys.has(key));
    try {
      persist(nextIds);
      setIds(nextIds);
    } catch (error) {
      reportFailure(show, operation, error);
    }
  }, [operation, ids, persist, sessionItems, setIds, show]);
}

// Owns the locally-persisted chat preferences (archived / pinned) and the
// pin toggle. Backend-owned chats round-trip their flags through the API;
// local cards keep their ids in localStorage.
export function useChatSessionPrefs({ dispatch, getApiUrl, show }) {
  const [archivedSessions, setArchivedSessions] = useState(() => loadStoredArchivedChatSessions());
  const [pinnedSessionIds, setPinnedSessionIds] = useState(() => loadStoredPinnedSessionIds());

  const togglePinnedSession = useCallback((sessionItem) => {
    if (!sessionItem?.pinKey) {
      return;
    }

    if (sessionItem.source === 'backend' && sessionItem.threadId) {
      dispatch(updateChatSessionMetadata({
        chatsUrl: getApiUrl('chats'),
        threadId: sessionItem.threadId,
        patch: { pinned: !sessionItem.pinned },
      })).unwrap()
        .catch((error) => reportFailure(show, 'Update pinned chats', error));
      return;
    }

    setPinnedSessionIds((currentPinnedSessionIds) => {
      const nextPinnedSessionIds = currentPinnedSessionIds.includes(sessionItem.pinKey)
        ? currentPinnedSessionIds.filter((pinKey) => pinKey !== sessionItem.pinKey)
        : [sessionItem.pinKey, ...currentPinnedSessionIds];

      try {
        persistPinnedSessionIds(nextPinnedSessionIds);
        return nextPinnedSessionIds;
      } catch (error) {
        reportFailure(show, 'Update pinned chats', error);
        return currentPinnedSessionIds;
      }
    });
  }, [dispatch, getApiUrl, show]);

  return {
    archivedSessions,
    setArchivedSessions,
    pinnedSessionIds,
    setPinnedSessionIds,
    togglePinnedSession,
  };
}
