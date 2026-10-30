import { createAcknowledgedThunk, requestAcknowledged } from '../../store/async';
import { failureMessage, isCancellation } from '../../lib/failures';
import { UI_EVENT } from '../../realtime/eventVocabulary';

export const initialState = {
  channels: {
    items: [],
    credentialStorage: {},
    loaded: false,
    loading: false,
    error: null,
    requestId: null,
    requests: {},
    testsByChannelId: {},
  },
};

export const fetchChannels = createAcknowledgedThunk(
  'uiData/fetchChannels',
  ({ channelsUrl }, { requestId }) => requestAcknowledged(channelsUrl, { requestId }),
);

export const patchChannel = createAcknowledgedThunk(
  'uiData/patchChannel',
  ({ channelsUrl, channelId, patch }, { requestId }) => requestAcknowledged(
    `${channelsUrl}/${encodeURIComponent(channelId)}`,
    { method: 'PATCH', body: patch, requestId },
  ),
);

export const testChannel = createAcknowledgedThunk(
  'uiData/testChannel',
  ({ channelsUrl, channelId }, { requestId }) => requestAcknowledged(
    `${channelsUrl}/${encodeURIComponent(channelId)}/test`,
    { method: 'POST', requestId },
  ),
);

export const reconnectChannel = createAcknowledgedThunk(
  'uiData/reconnectChannel',
  ({ channelsUrl, channelId }, { requestId }) => requestAcknowledged(
    `${channelsUrl}/${encodeURIComponent(channelId)}/reconnect`,
    { method: 'POST', requestId },
  ),
);

const snapshotOperations = [patchChannel, reconnectChannel];

function requestStarted(state, action, type) {
  state.channels.requests[action.meta.requestId] = {
    type,
    channelId: action.meta.arg?.channelId || null,
    loading: true,
    error: null,
  };
}

function requestRejected(state, action) {
  const request = state.channels.requests[action.meta.requestId];
  if (!request) return;
  request.loading = false;
  request.error = isCancellation(action.error)
    ? null : failureMessage(action.error?.message ? action.error : 'Channel request failed').message;
}

function completeRequest(state, requestId) {
  if (!requestId || !state.channels.requests[requestId]) return;
  state.channels.requests[requestId].loading = false;
  state.channels.requests[requestId].error = null;
}

export const eventHandlers = {
  [UI_EVENT.CHANNELS_SNAPSHOT]: (state, payload) => {
    state.channels.items = Array.isArray(payload.channels) ? payload.channels : [];
    state.channels.credentialStorage = payload.credential_storage
      && typeof payload.credential_storage === 'object'
      && !Array.isArray(payload.credential_storage)
      ? payload.credential_storage : {};
    state.channels.loaded = true;
    if (payload.request_id && payload.request_id === state.channels.requestId) {
      state.channels.loading = false;
      state.channels.error = null;
      state.channels.requestId = null;
    }
    completeRequest(state, payload.request_id);
  },
  [UI_EVENT.CHANNEL_TESTED]: (state, payload) => {
    const channelId = payload.channel_id;
    if (typeof channelId === 'string' && channelId) {
      state.channels.testsByChannelId[channelId] = {
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
    .addCase(fetchChannels.pending, (state, action) => {
      state.channels.loading = true;
      state.channels.error = null;
      state.channels.requestId = action.meta.requestId;
    })
    .addCase(fetchChannels.rejected, (state, action) => {
      if (state.channels.requestId !== action.meta.requestId) return;
      state.channels.loading = false;
      state.channels.error = isCancellation(action.error)
        ? null : failureMessage(action.error?.message ? action.error : 'Failed to load channels').message;
      state.channels.requestId = null;
    });
  for (const operation of snapshotOperations) {
    builder
      .addCase(operation.pending, (state, action) => requestStarted(state, action, operation.typePrefix))
      .addCase(operation.rejected, requestRejected);
  }
  builder
    .addCase(testChannel.pending, (state, action) => {
      requestStarted(state, action, testChannel.typePrefix);
      const channelId = action.meta.arg?.channelId;
      if (channelId) delete state.channels.testsByChannelId[channelId];
    })
    .addCase(testChannel.rejected, requestRejected);
  return builder;
}

export const selectChannels = (state) => state.uiData.channels.items;
export const selectChannelsState = (state) => state.uiData.channels;
export const selectChannelRequest = (state, requestId) => (requestId
  ? state.uiData.channels.requests[requestId] || null : null);
export const selectChannelTest = (state, channelId) => (channelId
  ? state.uiData.channels.testsByChannelId[channelId] || null : null);
