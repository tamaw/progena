import { createOperationRequestId, requestAcknowledged } from '../../store/http';
import { realtimeClientId, waitForRealtimeClient } from '../../realtime/realtimeClient';
import { subscribeUiEvent } from '../../realtime/realtimeEvents';

let activeWatch = null;
let requests = Promise.resolve();

function submit(watch, subscribed) {
  const selection = subscribed ? { kind: watch.kind, run_id: watch.runId } : {};
  const operation = requests.catch(() => {}).then(async () => {
    await waitForRealtimeClient();
    if (subscribed && activeWatch !== watch) throw new Error('Run trace is no longer viewed');
    return requestAcknowledged(watch.subscriptionUrl, {
      method: 'PUT',
      requestId: createOperationRequestId(),
      body: { client_id: realtimeClientId, ...selection },
    });
  });
  requests = operation;
  return operation;
}

function subscribe(watch, force = false) {
  if (!force && watch.subscription) return watch.subscription;
  const operation = submit(watch, true);
  watch.subscription = operation;
  operation.catch(() => {
    if (watch.subscription === operation) watch.subscription = null;
  });
  return operation;
}

subscribeUiEvent((event) => {
  if (event.type === 'connection.ready' && activeWatch) {
    void subscribe(activeWatch, true).catch(() => {});
  }
});

export function watchRunTrace(subscriptionUrl, kind, runId) {
  const watch = { subscriptionUrl, kind, runId, subscription: null };
  activeWatch = watch;
  void subscribe(watch).catch(() => {});
  return () => {
    if (activeWatch !== watch) return;
    activeWatch = null;
    void submit(watch, false).catch(() => {});
  };
}
