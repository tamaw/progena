import { UI_EVENT } from '../../realtime/eventVocabulary';
import { createOperationRequestId, requestAcknowledged } from '../../store/http';
import { realtimeClientId, waitForRealtimeClient } from '../../realtime/realtimeClient';
import { subscribeUiEvent } from '../../realtime/realtimeEvents';

const watches = new Map();

async function submit(watch, subscribed) {
  await waitForRealtimeClient();
  return requestAcknowledged(`${watch.tasksUrl}/subscription`, {
    method: 'PUT',
    requestId: createOperationRequestId(),
    body: { client_id: realtimeClientId, subscribed },
  });
}

function subscribe(watch) {
  if (watch.request) return watch.request;
  const request = submit(watch, true);
  watch.request = request;
  request.catch(() => {}).finally(() => {
    if (watch.request === request) watch.request = null;
  });
  return request;
}

subscribeUiEvent((event) => {
  if (event.type !== UI_EVENT.CONNECTION_READY) return;
  for (const watch of watches.values()) void subscribe(watch);
});

export function watchBackgroundTasks(tasksUrl) {
  const existing = watches.get(tasksUrl);
  if (existing) {
    existing.count += 1;
    return () => release(existing);
  }
  const watch = { tasksUrl, count: 1, request: null };
  watches.set(tasksUrl, watch);
  void subscribe(watch);
  return () => release(watch);
}

function release(watch) {
  watch.count -= 1;
  if (watch.count > 0 || watches.get(watch.tasksUrl) !== watch) return;
  watches.delete(watch.tasksUrl);
  void submit(watch, false).catch(() => {});
}
