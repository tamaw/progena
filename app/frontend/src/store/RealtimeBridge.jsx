import { useEffect, useRef } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { useConfig } from '../context/ConfigContext';
import { startRealtimeStream, stopRealtimeStream } from './realtimeListener';
import { selectRealtime } from './realtimeSlice';
import { fetchChatSessions } from './uiDataSlice';

export default function RealtimeBridge() {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const realtime = useSelector(selectRealtime);
  const requestedChatsUrlRef = useRef(null);
  const streamUrl = getApiUrl('stream');
  const chatsUrl = getApiUrl('chats');

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

  return null;
}
