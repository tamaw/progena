import { useEffect, useRef } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useConfig } from '../context/ConfigContext';
import { startRealtimeStream, stopRealtimeStream } from './realtimeListener';
import { selectRealtimeConnected } from './realtimeSlice';
import { fetchChatSessions, fetchInbox, selectGeneratingChatIds } from './uiDataSlice';

function selectHasActiveChatGeneration(state) {
  const selectedGeneratingChatIds = selectGeneratingChatIds(state);
  const generatingChatIds = Array.isArray(selectedGeneratingChatIds)
    ? selectedGeneratingChatIds
    : [];
  const responseThreadsByRequestId = state?.uiData?.chatRuntime?.responseThreadsByRequestId || {};
  return generatingChatIds.length > 0 || Object.keys(responseThreadsByRequestId).length > 0;
}

export default function RealtimeBridge() {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const realtimeConnected = useSelector(selectRealtimeConnected);
  const hasActiveGeneration = useSelector(selectHasActiveChatGeneration);
  const requestedChatsUrlRef = useRef(null);
  const requestedInboxUrlRef = useRef(null);
  const streamUrl = getApiUrl('stream');
  const chatsUrl = getApiUrl('chats');
  const inboxUrl = getApiUrl('inbox');

  useEffect(() => {
    dispatch(startRealtimeStream({ streamUrl }));
    return () => {
      dispatch(stopRealtimeStream());
    };
  }, [dispatch, streamUrl]);

  useEffect(() => {
    if (!realtimeConnected) {
      requestedChatsUrlRef.current = null;
      return;
    }
    // Reconnect recovery needs a fresh roster because EventSource cannot
    // request replay IDs. Delay that large Redux snapshot until any foreground
    // or background chat streaming settles so it cannot block text delivery on the main thread.
    if (hasActiveGeneration) {
      return;
    }
    if (requestedChatsUrlRef.current === chatsUrl) {
      return;
    }
    requestedChatsUrlRef.current = chatsUrl;
    dispatch(fetchChatSessions({ chatsUrl, includeArchived: true }));
  }, [chatsUrl, dispatch, hasActiveGeneration, realtimeConnected]);

  // Seed the inbox globally so the nav unread badge is correct before the
  // Inbox page is ever visited; SSE snapshots keep it fresh thereafter.
  useEffect(() => {
    if (!realtimeConnected) {
      requestedInboxUrlRef.current = null;
      return;
    }
    if (requestedInboxUrlRef.current === inboxUrl) {
      return;
    }
    requestedInboxUrlRef.current = inboxUrl;
    dispatch(fetchInbox({ inboxUrl }));
  }, [dispatch, inboxUrl, realtimeConnected]);

  return null;
}
