import { UI_EVENT } from '../../realtime/eventVocabulary';
import { createOperationRequestId, requestAcknowledged } from '../../store/http';
import { realtimeClientId, waitForRealtimeClient } from '../../realtime/realtimeClient';
import { subscribeUiEvent } from '../../realtime/realtimeEvents';

const watches = new Map();
// Task URLs whose interest a chat detail request installed on the server and
// no release has removed since; the next watch adopts it instead of asking.
const serverRegistrations = new Set();
let requests = Promise.resolve();
// A view re-renders through brief gaps (an open chat reloading its detail);
// releasing at once would race the interest that reload registers.
const RELEASE_DELAY_MS = 1000;

function submit(watch, subscribed) {
  const operation = requests.catch(() => {}).then(async () => {
    await waitForRealtimeClient();
    if (subscribed && watches.get(watch.tasksUrl) !== watch) {
      throw new Error('Background tasks are no longer viewed');
    }
    return requestAcknowledged(`${watch.tasksUrl}/subscription`, {
      method: 'PUT',
      requestId: createOperationRequestId(),
      body: { client_id: realtimeClientId, subscribed },
    });
  });
  requests = operation;
  return operation;
}

function subscribe(watch, reconnect = false) {
  if (watch.request && !reconnect) return watch.request;
  const request = submit(watch, true);
  watch.request = request;
  request.catch(() => {}).finally(() => {
    if (watch.request === request) watch.request = null;
  });
  return request;
}

subscribeUiEvent((event) => {
  if (event.type !== UI_EVENT.CONNECTION_READY) return;
  // A new connection starts with no server interests.
  serverRegistrations.clear();
  for (const watch of watches.values()) void subscribe(watch, true);
});

/** A chat detail request was acknowledged: the server now holds this tab's interest. */
export function noteServerRegistration(tasksUrl) {
  const watch = watches.get(tasksUrl);
  if (watch) {
    // A pending release would undo the registration that just happened.
    clearTimeout(watch.releaseTimer);
    watch.releaseTimer = null;
    return;
  }
  serverRegistrations.add(tasksUrl);
}

/** The detail request that would have installed the interest failed. */
export function forgetServerRegistration(tasksUrl) {
  serverRegistrations.delete(tasksUrl);
}

export function watchBackgroundTasks(tasksUrl) {
  const existing = watches.get(tasksUrl);
  if (existing) {
    clearTimeout(existing.releaseTimer);
    existing.releaseTimer = null;
    existing.count += 1;
    return () => release(existing);
  }
  const watch = { tasksUrl, count: 1, request: null, releaseTimer: null };
  watches.set(tasksUrl, watch);
  if (!serverRegistrations.delete(tasksUrl)) void subscribe(watch);
  return () => release(watch);
}

function release(watch) {
  watch.count -= 1;
  if (watch.count > 0 || watches.get(watch.tasksUrl) !== watch) return;
  watch.releaseTimer = setTimeout(() => {
    if (watch.count > 0 || watches.get(watch.tasksUrl) !== watch) return;
    watches.delete(watch.tasksUrl);
    serverRegistrations.delete(watch.tasksUrl);
    void submit(watch, false).catch(() => {});
  }, RELEASE_DELAY_MS);
}
