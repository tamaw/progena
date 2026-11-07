import { useEffect } from 'react';
import { useDispatch } from 'react-redux';
import { useConfig } from '../context/ConfigContext';
import { holdTopic } from '../realtime/streamTopics';

/**
 * Watch a stream topic while mounted. A falsy topic watches nothing. The
 * registration survives reconnects (`realtime/streamTopics`), but events missed
 * while disconnected are not replayed: the page must reload its data when the
 * stream becomes ready again.
 */
export default function useStreamTopic(topic) {
  const dispatch = useDispatch();
  const { getApiUrl } = useConfig();
  const streamUrl = getApiUrl('stream');

  useEffect(() => {
    if (!topic) return undefined;
    return holdTopic(topic, { dispatch, streamUrl });
  }, [dispatch, streamUrl, topic]);
}
