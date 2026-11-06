import { failPendingOp } from './realtimeSlice';
import { realtimeClientId } from './realtimeClient';

// A request the server accepted (HTTP 202) is settled by an event on this tab's
// stream. If the stream drops first, that event can be sent to a queue that is
// already closed. Instead of failing every pending request on a disconnect, the
// tab keeps them and, once the stream is ready again, asks the server to
// re-deliver the replies it recorded (`POST /api/ui/stream/resolve`). Replies
// then arrive as ordinary events, so the normal event path settles each request.
//
//  - Same server process: replies are replayed; ids the server has no record of
//    are still being worked on and arrive live, bounded by a deadline.
//  - New server process (`boot_id` changed): the accepted requests died with the
//    old one; their outcome is unknowable, so they fail with a clear message.
//  - No reconnect within the deadline: they fail, as they always did.

export const RECOVERY_DEADLINE_MS = 60000;
const RESOLVE_BATCH = 64;

let lastBootId = null;
let disconnectTimer = null;
const recoveryTimers = new Set();
// Requests whose 202 had not arrived when the stream dropped: their reply may
// already have been sent into the gap, so ask for it as soon as they acknowledge.
const carried = new Set();
let resolveUrl = null;

function pendingOps(listenerApi) {
  return Object.values(listenerApi.getState().realtime.pendingOps || {});
}

function failIds(listenerApi, requestIds, error) {
  const pending = listenerApi.getState().realtime.pendingOps || {};
  for (const requestId of requestIds) {
    if (pending[requestId]) listenerApi.dispatch(failPendingOp({ requestId, error }));
    carried.delete(requestId);
  }
}

function failAfterDeadline(listenerApi, requestIds, error) {
  if (requestIds.length === 0) return;
  const timer = setTimeout(() => {
    recoveryTimers.delete(timer);
    failIds(listenerApi, requestIds, error);
  }, RECOVERY_DEADLINE_MS);
  recoveryTimers.add(timer);
}

/** The stream dropped: keep pending requests, but not forever. */
export function holdPendingForRecovery(listenerApi) {
  if (disconnectTimer !== null || pendingOps(listenerApi).length === 0) return;
  disconnectTimer = setTimeout(() => {
    disconnectTimer = null;
    failIds(
      listenerApi,
      pendingOps(listenerApi).map((operation) => operation.requestId),
      'Connection lost before the server replied.',
    );
  }, RECOVERY_DEADLINE_MS);
}

async function resolveOutcomes(listenerApi, requestIds) {
  if (!resolveUrl) return;
  for (let start = 0; start < requestIds.length; start += RESOLVE_BATCH) {
    const batch = requestIds.slice(start, start + RESOLVE_BATCH);
    try {
      const response = await fetch(resolveUrl, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ client_id: realtimeClientId, request_ids: batch }),
      });
      if (!response.ok) throw new Error(`resolve returned ${response.status}`);
      const { unknown = [], lost = [] } = await response.json();
      failIds(listenerApi, lost, 'The result of this request was too large to recover; reload to see its effect.');
      failAfterDeadline(listenerApi, unknown, 'The server did not report the result of this request.');
    } catch {
      // The stream is probably down again; the next ready asks again, and the
      // deadline still bounds the wait.
      failAfterDeadline(listenerApi, batch, 'Connection lost before the server replied.');
    }
  }
}

/**
 * The stream is ready. Recover what a drop left pending. The first connection
 * has nothing to recover.
 */
export async function recoverOnReady(listenerApi, streamUrl, bootId) {
  clearTimeout(disconnectTimer);
  disconnectTimer = null;
  resolveUrl = `${streamUrl}/resolve`;
  const previousBootId = lastBootId;
  if (bootId) lastBootId = bootId;
  if (previousBootId === null) return;

  const operations = pendingOps(listenerApi);
  const acknowledged = operations.filter((operation) => operation.status === 'pending');
  if (bootId && bootId !== previousBootId) {
    carried.clear();
    failIds(
      listenerApi,
      acknowledged.map((operation) => operation.requestId),
      'The server restarted before this request finished. Check its result, then try again.',
    );
    return;
  }
  for (const operation of operations) {
    if (operation.status === 'awaiting_acknowledgement') carried.add(operation.requestId);
  }
  await resolveOutcomes(listenerApi, acknowledged.map((operation) => operation.requestId));
}

/** A carried request just got its 202: its reply may already be in the gap. */
export function noteAcknowledged(listenerApi, requestId) {
  if (!carried.delete(requestId)) return;
  if (listenerApi.getState().realtime.pendingOps?.[requestId]) {
    resolveOutcomes(listenerApi, [requestId]);
  }
}

/** The request failed on its own; nothing left to recover. */
export function forgetRequest(requestId) {
  carried.delete(requestId);
}

/** Test seam: forget the boot id and every carried request. */
export function resetRequestRecoveryForTest() {
  cancelRecovery();
  lastBootId = null;
  resolveUrl = null;
}

/** An explicit stop: drop every timer. The caller fails what is pending. */
export function cancelRecovery() {
  clearTimeout(disconnectTimer);
  disconnectTimer = null;
  for (const timer of recoveryTimers) clearTimeout(timer);
  recoveryTimers.clear();
  carried.clear();
}
