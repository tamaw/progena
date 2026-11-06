import { useEffect, useRef } from 'react';
import { onStreamReady } from '../realtime/streamLifecycle';

/**
 * Run `callback({ reconnect })` each time the stream becomes ready while this
 * component is mounted: the moment to re-request detail the page derived from
 * events it may have missed. It does not run at mount; mount-time loading
 * belongs to the page's own effect. Reads the latest callback, so it never
 * needs to be memoised. The stream is ready when the callback runs, but the
 * component has not re-rendered yet: its `streamReady`/`connected` values are
 * from before the reconnect, so never gate the request on them here.
 */
export default function useStreamResync(callback) {
  const callbackRef = useRef(callback);
  useEffect(() => {
    callbackRef.current = callback;
  }, [callback]);

  useEffect(() => onStreamReady((context) => callbackRef.current(context)), []);
}
