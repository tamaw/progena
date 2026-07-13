import { createAsyncThunk } from '@reduxjs/toolkit';
import { createOperationRequestId, requestAcknowledged } from '../http';

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
      requestState[errorKey] = action.error?.message || errorMessage;
      requestState[requestIdKey] = null;
      onRejected?.(state, action);
    });
  return builder;
}

export { requestAcknowledged };
