import { createAcknowledgedThunk, requestAcknowledged } from '../store/async';
import { isRealtimeClientReady, realtimeClientId, setRealtimeClientReady } from './realtimeClient';
/**
 * Register or release this tab's interest in a topic. Topic events reach only
 * tabs that watch the topic.
 */
export const setStreamTopic = createAcknowledgedThunk(
  'realtime/setStreamTopic',
  async ({ streamUrl, topic, watching }, { requestId }) => requestAcknowledged(`${streamUrl}/topics`, {
    method: 'PUT',
    body: { client_id: realtimeClientId, topic, watching },
    requestId,
  }),
);

// How long a topic stays registered after its last holder leaves. A page that
// remounts immediately (StrictMode, a route change within the same section)
// keeps the registration instead of sending a release followed by a re-join
// that the network could reorder.
const RELEASE_DELAY_MS = 200;

const holders = new Map();
const releaseTimers = new Map();
const registrations = new Map();
const retryTimers = new Map();
let lastContext = null;

function registrationFailed(topic) {
  if ((holders.get(topic) ?? 0) === 0 || retryTimers.has(topic)) return;
  const timer = setTimeout(() => {
    retryTimers.delete(topic);
    if ((holders.get(topic) ?? 0) > 0) registerTopic(topic);
  }, 250);
  retryTimers.set(topic, timer);
}

function completeRegistration(topic, registration) {
  if (registrations.get(topic) !== registration) return;
  registrations.delete(topic);
  if (registrations.size === 0) setRealtimeClientReady(true);
}

function registerTopic(topic) {
  if (!lastContext || (holders.get(topic) ?? 0) === 0) return Promise.resolve();
  if (registrations.has(topic)) return registrations.get(topic);

  // Finite snapshot requests wait for this gate. It closes before the PUT and
  // reopens only after every currently held topic has registered.
  setRealtimeClientReady(false);
  const { dispatch, streamUrl } = lastContext;
  const registration = Promise.resolve(dispatch(setStreamTopic({ streamUrl, topic, watching: true })))
    .then((result) => (typeof result?.unwrap === 'function' ? result.unwrap() : result))
    .catch(() => {
      registrationFailed(topic);
    })
    .finally(() => completeRegistration(topic, registration));
  registrations.set(topic, registration);
  return registration;
}

function send(topic, watching) {
  if (!lastContext || (!isRealtimeClientReady() && registrations.size === 0)) return;
  const { dispatch, streamUrl } = lastContext;
  if (watching) {
    registerTopic(topic);
    return;
  }
  dispatch(setStreamTopic({ streamUrl, topic, watching: false }));
}

/**
 * Register every current hold before the transport exposes stream readiness.
 * `realtimeListener` awaits this barrier before resync handlers may fetch.
 */
export function registerHeldTopics(context) {
  lastContext = context;
  const held = [...holders].filter(([, count]) => count > 0);
  if (held.length === 0) return null;
  // The server gives every replacement connection an empty topic set.
  registrations.clear();
  return Promise.all(held.map(([topic]) => registerTopic(topic)));
}

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
      const retry = retryTimers.get(topic);
      if (retry !== undefined) {
        clearTimeout(retry);
        retryTimers.delete(topic);
      }
      send(topic, false);
    }, RELEASE_DELAY_MS));
  };
}

export function resetStreamTopicsForTest() {
  for (const timer of releaseTimers.values()) clearTimeout(timer);
  for (const timer of retryTimers.values()) clearTimeout(timer);
  releaseTimers.clear();
  retryTimers.clear();
  holders.clear();
  registrations.clear();
  lastContext = null;
}
