import { createAsyncThunk } from '@reduxjs/toolkit';
import { createOperationRequestId, requestAcknowledged } from '../http';
import { failureMessage, isCancellation } from '../../lib/failures';

/** Build a finite UI request with one generated ID and its 202 acknowledgement. */
export function createAcknowledgedThunk(type, request) {
  return createAsyncThunk(
    type,
    async (args = {}, { requestId }) => request(args, { requestId }),
    { idGenerator: createOperationRequestId },
  );
}

/** Add the common loading/error lifecycle while retaining per-domain stale guards. */
export function addRequestLifecycle(builder, thunk, {
  select,
  errorMessage,
  loadingKey = 'loading',
  errorKey = 'error',
  requestIdKey = 'requestId',
  onPending,
  onFulfilled,
  onRejected,
}) {
  builder
    .addCase(thunk.pending, (state, action) => {
      const requestState = select(state);
      requestState[loadingKey] = true;
      requestState[errorKey] = null;
      requestState[requestIdKey] = action.meta.requestId;
      onPending?.(state, action);
    })
    .addCase(thunk.fulfilled, (state, action) => {
      onFulfilled?.(state, action);
    })
    .addCase(thunk.rejected, (state, action) => {
      const requestState = select(state);
      if (requestState[requestIdKey] !== action.meta.requestId) {
        return;
      }
      requestState[loadingKey] = false;
      requestState[errorKey] = isCancellation(action.error)
        ? null : failureMessage(action.error?.message ? action.error : errorMessage).message;
      requestState[requestIdKey] = null;
      onRejected?.(state, action);
    });
  return builder;
}

/**
 * Add per-entity request state for data that arrives through a causal stream
 * event. The HTTP 202 only acknowledges dispatch; it never completes loading.
 */
export function addKeyedRequestLifecycle(builder, thunk, {
  selectRequests,
  keyFromArgs,
  errorMessage,
}) {
  builder
    .addCase(thunk.pending, (state, action) => {
      const key = keyFromArgs(action.meta.arg);
      if (!key) {
        return;
      }
      selectRequests(state)[key] = {
        loading: true,
        error: null,
        requestId: action.meta.requestId,
      };
    })
    .addCase(thunk.rejected, (state, action) => {
      const key = keyFromArgs(action.meta.arg);
      const requestState = key ? selectRequests(state)[key] : null;
      if (!requestState || requestState.requestId !== action.meta.requestId) {
        return;
      }
      requestState.loading = false;
      requestState.error = isCancellation(action.error) ? null : action.error?.message ? action.error : errorMessage;
      requestState.requestId = null;
    });
  return builder;
}

/** Complete a keyed load only when its matching data event reaches the cache. */
export function completeKeyedRequest(requests, key, requestId) {
  const requestState = key ? requests[key] : null;
  if (!requestState || requestState.requestId !== requestId) {
    return;
  }
  requestState.loading = false;
  requestState.error = null;
  requestState.requestId = null;
}

const EMPTY_KEYED_REQUEST = Object.freeze({
  loading: false,
  error: null,
  requestId: null,
});

/** Return an idle state for unseen cache keys rather than inventing a load. */
export function selectKeyedRequest(requests, key) {
  return key ? requests[key] || EMPTY_KEYED_REQUEST : EMPTY_KEYED_REQUEST;
}

export { requestAcknowledged };
