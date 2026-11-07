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
const recoveryTimers = new Map();
// Requests whose 202 had not arrived when the stream dropped: their reply may
// already have been sent into the gap, so ask for it as soon as they acknowledge.
const carried = new Set();
const requestBootIds = new Map();
const staleAcknowledgements = new Set();
let resolveUrl = null;

function pendingOps(listenerApi) {
  return Object.values(listenerApi.getState().realtime.pendingOps || {});
}

function clearDeadline(requestId) {
  const timer = recoveryTimers.get(requestId);
  if (timer !== undefined) clearTimeout(timer);
  recoveryTimers.delete(requestId);
}

function failIds(listenerApi, requestIds, error) {
  const pending = listenerApi.getState().realtime.pendingOps || {};
  for (const requestId of requestIds) {
    if (pending[requestId]) listenerApi.dispatch(failPendingOp({ requestId, error }));
    carried.delete(requestId);
    requestBootIds.delete(requestId);
    clearDeadline(requestId);
  }
}

function ensureDeadline(listenerApi, requestId, error) {
  if (recoveryTimers.has(requestId)) return;
  const timer = setTimeout(() => {
    recoveryTimers.delete(requestId);
    failIds(listenerApi, [requestId], error);
  }, RECOVERY_DEADLINE_MS);
  recoveryTimers.set(requestId, timer);
}

/** The stream dropped: keep pending requests, but not forever. */
export function holdPendingForRecovery(listenerApi) {
  for (const operation of pendingOps(listenerApi)) {
    ensureDeadline(listenerApi, operation.requestId, 'Connection lost before the server replied.');
  }
}

async function resolveOutcomes(listenerApi, requestIds) {
  if (!resolveUrl) return;
  for (let start = 0; start < requestIds.length; start += RESOLVE_BATCH) {
    const batch = requestIds.slice(start, start + RESOLVE_BATCH);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), RECOVERY_DEADLINE_MS);
    try {
      const response = await fetch(resolveUrl, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ client_id: realtimeClientId, request_ids: batch }),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`resolve returned ${response.status}`);
      const { unknown = [], lost = [] } = await response.json();
      failIds(listenerApi, lost, 'The result of this request was too large to recover; reload to see its effect.');
      for (const requestId of unknown) {
        ensureDeadline(listenerApi, requestId, 'The server did not report the result of this request.');
      }
    } catch {
      // The next ready asks again. Individual deadlines remain in force while
      // this request or its response is stalled.
      for (const requestId of batch) {
        ensureDeadline(listenerApi, requestId, 'Connection lost before the server replied.');
      }
    } finally {
      clearTimeout(timeout);
    }
  }
}

/**
 * The stream is ready. Recover what a drop left pending. The first connection
 * has nothing to recover.
 */
export async function recoverOnReady(listenerApi, streamUrl, bootId) {
  resolveUrl = `${streamUrl}/resolve`;
  const previousBootId = lastBootId;
  if (bootId) lastBootId = bootId;
  if (previousBootId === null) return;

  const operations = pendingOps(listenerApi);
  for (const operation of operations) {
    ensureDeadline(listenerApi, operation.requestId, 'Connection lost before the server replied.');
  }
  const acknowledged = operations.filter((operation) => operation.status === 'pending');
  if (bootId && bootId !== previousBootId) {
    const failed = [];
    for (const operation of operations) {
      if (
        operation.status === 'pending'
        || requestBootIds.get(operation.requestId) === previousBootId
      ) {
        staleAcknowledgements.add(operation.requestId);
        failed.push(operation.requestId);
      }
    }
    failIds(
      listenerApi,
      failed,
      'The server restarted before this request finished. Check its result, then try again.',
    );
    return;
  }
  for (const operation of operations) {
    if (operation.status === 'awaiting_acknowledgement') carried.add(operation.requestId);
  }
  await resolveOutcomes(listenerApi, acknowledged.map((operation) => operation.requestId));
}

/** Record which server boot accepted an operation before its HTTP request starts. */
export function noteRequestStarted(requestId) {
  requestBootIds.set(requestId, lastBootId);
}

/** A carried request just got its 202: its reply may already be in the gap. */
export function noteAcknowledged(listenerApi, requestId) {
  if (!carried.delete(requestId)) return;
  if (listenerApi.getState().realtime.pendingOps?.[requestId]) {
    ensureDeadline(listenerApi, requestId, 'Connection lost before the server replied.');
    resolveOutcomes(listenerApi, [requestId]);
  }
}

/** True once for an acknowledgement sent by a server boot that has restarted. */
export function consumeStaleAcknowledgement(requestId) {
  if (!staleAcknowledgements.delete(requestId)) return false;
  requestBootIds.delete(requestId);
  clearDeadline(requestId);
  return true;
}

/** The request failed on its own; nothing left to recover. */
export function forgetRequest(requestId) {
  carried.delete(requestId);
  requestBootIds.delete(requestId);
  clearDeadline(requestId);
}

/** Test seam: forget the boot id and every carried request. */
export function resetRequestRecoveryForTest() {
  cancelRecovery();
  lastBootId = null;
  resolveUrl = null;
}

/** An explicit stop: drop every timer. The caller fails what is pending. */
export function cancelRecovery() {
  for (const timer of recoveryTimers.values()) clearTimeout(timer);
  recoveryTimers.clear();
  carried.clear();
  requestBootIds.clear();
  staleAcknowledgements.clear();
}
