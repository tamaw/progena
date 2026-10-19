import { createOperationRequestId, requestAcknowledged } from './http';
import { realtimeClientId, waitForRealtimeClient } from './realtimeClient';
import { subscribeUiEvent } from './realtimeEvents';

let activeWatch = null;
let requests = Promise.resolve();

function submit(watch, subscribed) {
  const selection = { ...watch.selection };
  const operation = requests.catch(() => {}).then(async () => {
    await waitForRealtimeClient();
    if (subscribed && activeWatch !== watch) throw new Error('Routine page is no longer viewed');
    return requestAcknowledged(`${watch.agentsUrl}/${encodeURIComponent(watch.agentId)}/run-subscription`, {
      method: 'PUT', requestId: createOperationRequestId(),
      body: { client_id: realtimeClientId, subscribed, ...selection },
    });
  });
  requests = operation;
  return operation;
}

// Re-register after reconnect; the server removes interests with dead sockets.
subscribeUiEvent((event) => {
  if (event.type === 'connection.ready' && activeWatch) {
    void submit(activeWatch, true).catch(() => {});
  }
});

export function watchRoutineRuns(agentsUrl, agentId) {
  const watch = { agentsUrl, agentId, selection: {}, selectionKey: null, selectionOperation: null };
  // Identical selections share one request: the page re-selects the same thread
  // on mount and after starting a run. Reconnects bypass this and always resubmit.
  const selectSubscribed = (selection) => {
    const key = JSON.stringify(selection);
    if (watch.selectionKey === key) return watch.selectionOperation;
    watch.selection = selection;
    watch.selectionKey = key;
    const operation = submit(watch, true);
    watch.selectionOperation = operation;
    operation.catch(() => {
      if (watch.selectionOperation === operation) watch.selectionKey = null;
    });
    return operation;
  };
  activeWatch = watch;
  void selectSubscribed({}).catch(() => {});
  return {
    select: (selection = {}) => selectSubscribed(selection),
    close() {
      if (activeWatch !== watch) return;
      activeWatch = null;
      void submit(watch, false).catch(() => {});
    },
  };
}
