import { useEffect, useRef } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useConfig } from '../context/ConfigContext';
import { startRealtimeStream, stopRealtimeStream } from '../realtime/realtimeListener';
import { selectRealtimeConnected, selectRealtimeStreamReady } from '../realtime/realtimeSlice';
import {
  fetchChatSessions,
  requestChatSessionReconciliation,
} from '../features/chat/state/chats';
import { fetchInbox } from '../features/inbox/state';
import { fetchModelProfiles } from '../features/settings/state/modelProfiles';
function selectReconnectChat(state) {
  const chats = state?.uiData?.chats;
  const threadId = chats?.active?.threadId;
  if (!threadId) return null;
  const responseThreads = state?.uiData?.chatRuntime?.responseThreadsByRequestId || {};
  return Object.values(responseThreads).includes(threadId) ? null : threadId;
}

function selectChatView(state) {
  return state?.uiData?.chats?.view || { q: '', sort: 'recent', filter: 'active' };
}

export default function RealtimeBridge() {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const realtimeConnected = useSelector(selectRealtimeConnected);
  const streamReady = useSelector(selectRealtimeStreamReady);
  const reconnectChatThreadId = useSelector(selectReconnectChat);
  const chatView = useSelector(selectChatView);
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

    dispatch(fetchChatSessions({
      chatsUrl,
      view: chatView,
      limit: 50,
    }));
    if (reconnectChatThreadId) {
      dispatch(requestChatSessionReconciliation({ chatsUrl, threadId: reconnectChatThreadId }));
    }
  }, [chatView, chatsUrl, dispatch, realtimeConnected, reconnectChatThreadId]);

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
