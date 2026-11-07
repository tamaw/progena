// One place that runs when the canonical stream becomes ready: the first
// connection and every reconnect. The server forgets a tab's topics and sends
// no replay of replies, so whatever a tab holds server-side (topic interest) or
// derived from missed events (open detail views) is re-established from here.
const handlers = new Set();
let readyCount = 0;

/**
 * Run `handler({ dispatch, streamUrl, reconnect })` on every stream-ready.
 * `reconnect` is false only for the first one in this page's lifetime.
 * Returns the unsubscribe function.
 */
export function onStreamReady(handler) {
  handlers.add(handler);
  return () => {
    handlers.delete(handler);
  };
}

export function notifyStreamReady(context) {
  const reconnect = readyCount > 0;
  readyCount += 1;
  for (const handler of [...handlers]) {
    try {
      handler({ ...context, reconnect });
    } catch {
      // One handler must not stop the others from resyncing.
    }
  }
}

/** Test seam: forget earlier connections so the next ready is a first one. */
export function resetStreamLifecycleForTest() {
  readyCount = 0;
}
