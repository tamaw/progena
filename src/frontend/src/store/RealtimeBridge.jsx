import { useEffect, useRef } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useConfig } from '../context/ConfigContext';
import { startRealtimeStream, stopRealtimeStream } from '../realtime/realtimeListener';
import { selectRealtimeConnected, selectRealtimeStreamReady } from '../realtime/realtimeSlice';
import {
  requestChatSessionReconciliation,
} from '../features/chat/state/chats';
import { fetchInboxCounts } from '../features/inbox/state';
import { fetchModelProfiles } from '../features/settings/state/modelProfiles';
function selectReconnectChat(state) {
  const chats = state?.uiData?.chats;
  const threadId = chats?.active?.threadId;
  if (!threadId) return null;
  const responseThreads = state?.uiData?.chatRuntime?.responseThreadsByRequestId || {};
  return Object.values(responseThreads).includes(threadId) ? null : threadId;
}


export default function RealtimeBridge() {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const realtimeConnected = useSelector(selectRealtimeConnected);
  const streamReady = useSelector(selectRealtimeStreamReady);
  const reconnectChatThreadId = useSelector(selectReconnectChat);
  const requestedInboxUrlRef = useRef(null);
  const requestedProfilesUrlRef = useRef(null);
  const hasConnectedRef = useRef(false);
  const wasConnectedRef = useRef(false);

  const streamUrl = getApiUrl('stream');
  const chatsUrl = getApiUrl('chats');
  const inboxUrl = getApiUrl('inbox');
  const modelProfilesUrl = getApiUrl('modelProfiles');

  useEffect(() => {
    dispatch(startRealtimeStream({ streamUrl }));
    return () => {
      dispatch(stopRealtimeStream());
    };
  }, [dispatch, streamUrl]);

  useEffect(() => {
    if (!realtimeConnected) {
      wasConnectedRef.current = false;
      return;
    }

    const reconnected = hasConnectedRef.current && !wasConnectedRef.current;
    hasConnectedRef.current = true;
    wasConnectedRef.current = true;
    if (!reconnected) return;

    if (reconnectChatThreadId) {
      dispatch(requestChatSessionReconciliation({ chatsUrl, threadId: reconnectChatThreadId }));
    }
  }, [chatsUrl, dispatch, realtimeConnected, reconnectChatThreadId]);

  // The shell needs only the authoritative unread count before Inbox is
  // visited. Sidebar pages request their own positional list ranges.
  useEffect(() => {
    if (!streamReady) {
      requestedInboxUrlRef.current = null;
      return;
    }
    if (requestedInboxUrlRef.current === inboxUrl) return;
    requestedInboxUrlRef.current = inboxUrl;
    dispatch(fetchInboxCounts({ inboxUrl }));
  }, [dispatch, inboxUrl, streamReady]);

  // Profiles serve both their management page and routine model selectors.
  useEffect(() => {
    if (!streamReady) {
      requestedProfilesUrlRef.current = null;
      return;
    }
    if (requestedProfilesUrlRef.current === modelProfilesUrl) return;
    requestedProfilesUrlRef.current = modelProfilesUrl;
    dispatch(fetchModelProfiles({ modelProfilesUrl }));
  }, [dispatch, modelProfilesUrl, streamReady]);

  return null;
}
