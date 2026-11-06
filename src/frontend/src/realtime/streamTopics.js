import { createAcknowledgedThunk, requestAcknowledged } from '../store/async';
import { isRealtimeClientReady, realtimeClientId, waitForRealtimeClient } from './realtimeClient';
import { onStreamReady } from './streamLifecycle';

/**
 * Register or release this tab's interest in a topic. Topic events reach only
 * tabs that watch the topic.
 */
export const setStreamTopic = createAcknowledgedThunk(
  'realtime/setStreamTopic',
  async ({ streamUrl, topic, watching }, { requestId }) => {
    await waitForRealtimeClient();
    return requestAcknowledged(`${streamUrl}/topics`, {
      method: 'PUT',
      body: { client_id: realtimeClientId, topic, watching },
      requestId,
    });
  },
);

// How long a topic stays registered after its last holder leaves. A page that
// remounts immediately (StrictMode, a route change within the same section)
// keeps the registration instead of sending a release followed by a re-join
// that the network could reorder.
const RELEASE_DELAY_MS = 200;

const holders = new Map();
const releaseTimers = new Map();
let lastContext = null;

function send(topic, watching) {
  if (!lastContext || !isRealtimeClientReady()) return;
  const { dispatch, streamUrl } = lastContext;
  dispatch(setStreamTopic({ streamUrl, topic, watching }));
}

// The server drops every topic when the stream closes, so each (re)connect
// registers everything still held. Nothing is released then: there is no stream
// to ask, and the new connection starts empty.
onStreamReady(({ dispatch, streamUrl }) => {
  lastContext = { dispatch, streamUrl };
  for (const [topic, count] of holders) {
    if (count > 0) dispatch(setStreamTopic({ streamUrl, topic, watching: true }));
  }
});

/**
 * Watch `topic` until the returned function is called. Reference counted, so
 * several pages can hold the same topic. While the stream is not ready the
 * topic is only recorded; the next stream-ready registers it.
 */
export function holdTopic(topic, context) {
  lastContext = context;
  const pending = releaseTimers.get(topic);
  if (pending !== undefined) {
    clearTimeout(pending);
    releaseTimers.delete(topic);
  }
  const count = holders.get(topic) ?? 0;
  holders.set(topic, count + 1);
  if (count === 0 && pending === undefined) send(topic, true);

  let released = false;
  return () => {
    if (released) return;
    released = true;
    const remaining = (holders.get(topic) ?? 1) - 1;
    if (remaining > 0) {
      holders.set(topic, remaining);
      return;
    }
    holders.set(topic, 0);
    releaseTimers.set(topic, setTimeout(() => {
      releaseTimers.delete(topic);
      holders.delete(topic);
      send(topic, false);
    }, RELEASE_DELAY_MS));
  };
}

/** Test seam: forget every held topic and pending release. */
export function resetStreamTopicsForTest() {
  for (const timer of releaseTimers.values()) clearTimeout(timer);
  releaseTimers.clear();
  holders.clear();
  lastContext = null;
}
