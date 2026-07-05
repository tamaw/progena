import { useCallback, useEffect, useState } from 'react';
import {
  loadStoredArchivedChatSessions,
  loadStoredPinnedSessionIds,
  loadStoredSharedSessionIds,
  persistPinnedSessionIds,
  persistSharedSessionIds,
} from '../components/chat/chatStorage';
import { updateChatSessionMetadata } from '../store/uiDataSlice';

// Prune persisted selection ids whose chats no longer exist. Drives both the
// pinned and shared id sets, which previously had two identical effects. Called
// from the page after `sessionItems` is derived (it depends on these ids).
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

// Owns the locally-persisted chat preferences (archived / pinned / shared) and
// the pin/share toggles. Backend-owned chats round-trip their flags through the
// API; local cards keep their ids in localStorage.
export function useChatSessionPrefs({ dispatch, getApiUrl, show }) {
  const [archivedSessions, setArchivedSessions] = useState(() => loadStoredArchivedChatSessions());
  const [pinnedSessionIds, setPinnedSessionIds] = useState(() => loadStoredPinnedSessionIds());
  const [sharedSessionIds, setSharedSessionIds] = useState(() => loadStoredSharedSessionIds());

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

  const toggleSharedSession = useCallback((sessionItem) => {
    if (!sessionItem?.pinKey) {
      return;
    }

    if (sessionItem.source === 'backend' && sessionItem.threadId) {
      dispatch(updateChatSessionMetadata({
        chatsUrl: getApiUrl('chats'),
        threadId: sessionItem.threadId,
        patch: { shared: !sessionItem.shared },
      })).unwrap()
        .catch((error) => show('error', 'Sharing failed', error.message));
      return;
    }

    setSharedSessionIds((currentSharedSessionIds) => {
      const isCurrentlyShared = currentSharedSessionIds.includes(sessionItem.pinKey);
      const nextSharedSessionIds = isCurrentlyShared
        ? currentSharedSessionIds.filter((shareKey) => shareKey !== sessionItem.pinKey)
        : [sessionItem.pinKey, ...currentSharedSessionIds];

      try {
        persistSharedSessionIds(nextSharedSessionIds);
        show(
          'success',
          isCurrentlyShared ? 'Chat unshared' : 'Chat shared',
          isCurrentlyShared
            ? 'Removed this chat from the shared list.'
            : 'Added this chat to the shared list.',
        );
        return nextSharedSessionIds;
      } catch (error) {
        show('error', 'Sharing failed', error.message);
        return currentSharedSessionIds;
      }
    });
  }, [dispatch, getApiUrl, show]);

  return {
    archivedSessions,
    setArchivedSessions,
    pinnedSessionIds,
    setPinnedSessionIds,
    sharedSessionIds,
    setSharedSessionIds,
    togglePinnedSession,
    toggleSharedSession,
  };
}
