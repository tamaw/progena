import { createAcknowledgedThunk, requestAcknowledged } from '../../../store/async';
import { failureMessage, isCancellation } from '../../../lib/failures';

export const initialState = {
  providers: {
    items: [],
    goModels: [],
    loaded: false,
    loading: false,
    error: null,
    requestId: null,
    snapshotRequestId: null,
    requests: {},
    testsByProviderId: {},
  },
};

export const fetchProviders = createAcknowledgedThunk(
  'uiData/fetchProviders',
  ({ providersUrl }, { requestId }) => requestAcknowledged(providersUrl, { requestId }),
);

export const createProvider = createAcknowledgedThunk(
  'uiData/createProvider',
  ({ providersUrl, provider }, { requestId }) => requestAcknowledged(providersUrl, {
    method: 'POST', body: provider, requestId,
  }),
);

export const patchProvider = createAcknowledgedThunk(
  'uiData/patchProvider',
  ({ providersUrl, providerId, patch }, { requestId }) => requestAcknowledged(
    `${providersUrl}/${encodeURIComponent(providerId)}`,
    { method: 'PATCH', body: patch, requestId },
  ),
);

export const deleteProvider = createAcknowledgedThunk(
  'uiData/deleteProvider',
  ({ providersUrl, providerId }, { requestId }) => requestAcknowledged(
    `${providersUrl}/${encodeURIComponent(providerId)}`,
    { method: 'DELETE', requestId },
  ),
);

export const testProviderConnection = createAcknowledgedThunk(
  'uiData/testProviderConnection',
  ({ providersUrl, providerId }, { requestId }) => requestAcknowledged(
    `${providersUrl}/${encodeURIComponent(providerId)}/test`,
    { method: 'POST', requestId },
  ),
);

const snapshotOperations = [createProvider, patchProvider, deleteProvider];

function requestStarted(state, action, type) {
  state.providers.requests[action.meta.requestId] = {
    type,
    providerId: action.meta.arg?.providerId || null,
    loading: true,
    error: null,
  };
}

function requestRejected(state, action) {
  const request = state.providers.requests[action.meta.requestId];
  if (!request) return;
  request.loading = false;
  request.error = isCancellation(action.error)
    ? null : failureMessage(action.error?.message ? action.error : 'Provider request failed').message;
}

function completeRequest(state, requestId) {
  if (!requestId || !state.providers.requests[requestId]) return;
  state.providers.requests[requestId].loading = false;
  state.providers.requests[requestId].error = null;
}

export const eventHandlers = {
  'providers.snapshot': (state, payload) => {
    state.providers.items = Array.isArray(payload.providers) ? payload.providers : [];
    state.providers.goModels = Array.isArray(payload.go_models) ? payload.go_models : [];
    state.providers.loaded = true;
    state.providers.snapshotRequestId = payload.request_id || null;
    if (!state.providers.loading || payload.request_id === state.providers.requestId) {
      state.providers.loading = false;
      state.providers.error = null;
      state.providers.requestId = null;
    }
    completeRequest(state, payload.request_id);
  },
  'providers.tested': (state, payload) => {
    const providerId = payload.provider_id;
    if (typeof providerId === 'string' && providerId) {
      state.providers.testsByProviderId[providerId] = {
        ok: payload.ok === true,
        error: typeof payload.error === 'string' ? payload.error : null,
        requestId: payload.request_id || null,
      };
    }
    completeRequest(state, payload.request_id);
  },
};

export function buildExtraReducers(builder) {
  builder
    .addCase(fetchProviders.pending, (state, action) => {
      state.providers.loading = true;
      state.providers.error = null;
      state.providers.requestId = action.meta.requestId;
    })
    .addCase(fetchProviders.rejected, (state, action) => {
      if (state.providers.requestId !== action.meta.requestId) return;
      state.providers.loading = false;
      state.providers.error = isCancellation(action.error)
        ? null : failureMessage(action.error?.message ? action.error : 'Failed to load providers').message;
      state.providers.requestId = null;
    });
  for (const operation of snapshotOperations) {
    builder
      .addCase(operation.pending, (state, action) => requestStarted(state, action, operation.typePrefix))
      .addCase(operation.rejected, requestRejected);
  }
  builder
    .addCase(testProviderConnection.pending, (state, action) => {
      requestStarted(state, action, testProviderConnection.typePrefix);
      const providerId = action.meta.arg?.providerId;
      if (providerId) delete state.providers.testsByProviderId[providerId];
    })
    .addCase(testProviderConnection.rejected, requestRejected);
  return builder;
}

export const selectProviders = (state) => state.uiData.providers.items;
export const selectGoResponsesModels = (state) => state.uiData.providers.goModels;
export const selectProvidersState = (state) => state.uiData.providers;
export const selectProviderRequest = (state, requestId) => (requestId
  ? state.uiData.providers.requests[requestId] || null : null);
export const selectProviderTest = (state, providerId) => (providerId
  ? state.uiData.providers.testsByProviderId[providerId] || null : null);
