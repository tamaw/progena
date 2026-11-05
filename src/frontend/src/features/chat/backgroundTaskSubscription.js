import { UI_EVENT } from '../../realtime/eventVocabulary';
import { createOperationRequestId, requestAcknowledged } from '../../store/http';
import { realtimeClientId, waitForRealtimeClient } from '../../realtime/realtimeClient';
import { subscribeUiEvent } from '../../realtime/realtimeEvents';

const watches = new Map();
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
  for (const watch of watches.values()) void subscribe(watch, true);
});

export function watchBackgroundTasks(tasksUrl, { serverRegistered = false } = {}) {
  const existing = watches.get(tasksUrl);
  if (existing) {
    clearTimeout(existing.releaseTimer);
    existing.releaseTimer = null;
    existing.count += 1;
    return () => release(existing);
  }
  const watch = { tasksUrl, count: 1, request: null, releaseTimer: null };
  watches.set(tasksUrl, watch);
  if (!serverRegistered) void subscribe(watch);
  return () => release(watch);
}

function release(watch) {
  watch.count -= 1;
  if (watch.count > 0 || watches.get(watch.tasksUrl) !== watch) return;
  watch.releaseTimer = setTimeout(() => {
    if (watch.count > 0 || watches.get(watch.tasksUrl) !== watch) return;
    watches.delete(watch.tasksUrl);
    void submit(watch, false).catch(() => {});
  }, RELEASE_DELAY_MS);
}
