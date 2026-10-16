import { useCallback, useEffect, useState } from 'react';
import {
  loadStoredArchivedChatSessions,
  loadStoredPinnedSessionIds,
  persistPinnedSessionIds,
} from '../components/chat/chatStorage';
import { updateChatSessionMetadata } from '../store/uiDataSlice';

// Prune persisted selection ids whose chats no longer exist. Drives the
// pinned id set. Called from the page after `sessionItems` is derived.
export function usePrunedSelection({ ids, setIds, persist, sessionItems, show, errorTitle }) {
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
      show('error', errorTitle, error.message);
    }
  }, [errorTitle, ids, persist, sessionItems, setIds, show]);
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
        .catch((error) => show('error', 'Pinning failed', error.message));
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
        show('error', 'Pinning failed', error.message);
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
