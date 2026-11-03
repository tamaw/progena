import { UI_EVENT } from '../../realtime/eventVocabulary';
import { createOperationRequestId, requestAcknowledged } from '../../store/http';
import { realtimeClientId, waitForRealtimeClient } from '../../realtime/realtimeClient';
import { subscribeUiEvent } from '../../realtime/realtimeEvents';

const watches = new Map();
let requests = Promise.resolve();

function submit(watch, subscribed) {
  const operation = requests.catch(() => {}).then(async () => {
    await waitForRealtimeClient();
    if (subscribed && watches.get(watch.interestId) !== watch) {
      throw new Error('Background run trace is no longer viewed');
    }
    return requestAcknowledged(watch.subscriptionUrl, {
      method: 'PUT',
      requestId: createOperationRequestId(),
      body: subscribed
        ? { client_id: realtimeClientId, interest_id: watch.interestId, kind: watch.kind, run_id: watch.runId }
        : { client_id: realtimeClientId, interest_id: watch.interestId },
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

export function watchBackgroundRunTrace(subscriptionUrl, kind, runId) {
  const interestId = `background:${kind}:${runId}`;
  const existing = watches.get(interestId);
  if (existing) {
    existing.count += 1;
    return () => release(existing);
  }
  const watch = { subscriptionUrl, kind, runId, interestId, count: 1, request: null };
  watches.set(interestId, watch);
  void subscribe(watch);
  return () => release(watch);
}

function release(watch) {
  watch.count -= 1;
  if (watch.count > 0 || watches.get(watch.interestId) !== watch) return;
  watches.delete(watch.interestId);
  void submit(watch, false).catch(() => {});
}
