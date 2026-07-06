import { useEffect, useRef } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useConfig } from '../context/ConfigContext';
import { startRealtimeStream, stopRealtimeStream } from './realtimeListener';
import { selectRealtime } from './realtimeSlice';
import { fetchChatSessions, fetchInbox } from './uiDataSlice';

export default function RealtimeBridge() {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const realtime = useSelector(selectRealtime);
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
    if (!realtime.connected) {
      requestedChatsUrlRef.current = null;
      return;
    }
    if (requestedChatsUrlRef.current === chatsUrl) {
      return;
    }
    requestedChatsUrlRef.current = chatsUrl;
    dispatch(fetchChatSessions({ chatsUrl, includeArchived: true }));
  }, [chatsUrl, dispatch, realtime.connected]);

  // Seed the inbox globally so the nav unread badge is correct before the
  // Inbox page is ever visited; SSE snapshots keep it fresh thereafter.
  useEffect(() => {
    if (!realtime.connected) {
      requestedInboxUrlRef.current = null;
      return;
    }
    if (requestedInboxUrlRef.current === inboxUrl) {
      return;
    }
    requestedInboxUrlRef.current = inboxUrl;
    dispatch(fetchInbox({ inboxUrl }));
  }, [dispatch, inboxUrl, realtime.connected]);

  return null;
}
