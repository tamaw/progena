/** Canvas catalogue and file state, backed by the canonical UI stream. */
import {
  addKeyedRequestLifecycle,
  addRequestLifecycle,
  completeKeyedRequest,
  createAcknowledgedThunk,
  requestAcknowledged,
  selectKeyedRequest,
} from '../../store/async';
import { withQuery, createOperationRequestId } from '../../store/http';
import { realtimeClientId, waitForRealtimeClient } from '../../realtime/realtimeClient';
import { subscribeUiEvent } from '../../realtime/realtimeEvents';
import { UI_EVENT } from '../../realtime/eventVocabulary';


export const initialState = {
  canvases: {
    items: [], loaded: false, loading: false, error: null, requestId: null,
    filesByName: {},
    filesRequestByName: {},
  },
};

export const fetchCanvases = createAcknowledgedThunk(
  'uiData/fetchCanvases',
  async ({ canvasesUrl }, { requestId }) => requestAcknowledged(canvasesUrl, { requestId }),
);
export const fetchCanvasFiles = createAcknowledgedThunk(
  'uiData/fetchCanvasFiles',
  async ({ canvasesUrl, name }, { requestId }) => {
    await waitForRealtimeClient();
    return requestAcknowledged(withQuery(`${canvasesUrl}/${encodeURIComponent(name)}/files`, { client_id: realtimeClientId }), { requestId });
  },
);
export const deleteCanvas = createAcknowledgedThunk(
  'uiData/deleteCanvas',
  async ({ canvasesUrl, name }, { requestId }) => {
    await waitForRealtimeClient();
    return requestAcknowledged(withQuery(`${canvasesUrl}/${encodeURIComponent(name)}`, { client_id: realtimeClientId }), { method: 'DELETE', requestId });
  },
);
export const reportCanvasStatus = createAcknowledgedThunk(
  'uiData/reportCanvasStatus',
  async ({ canvasesUrl, name, state, revision, errors }, { requestId }) => {
    await waitForRealtimeClient();
    return requestAcknowledged(`${canvasesUrl}/${encodeURIComponent(name)}/status`, {
      method: 'POST', requestId, body: { client_id: realtimeClientId, state, revision, errors },
    });
  },
);

/** Submit a targeted bridge request and resolve only its matching stream response. */
export async function requestCanvasEvent({ canvasesUrl, name, suffix, method = 'POST', body, eventType }) {
  await waitForRealtimeClient();
  const requestId = createOperationRequestId();
  return new Promise((resolve, reject) => {
    const unsubscribe = subscribeUiEvent((event) => {
      if (event?.type === eventType && event?.payload?.request_id === requestId) {
        unsubscribe();
        resolve(event.payload || {});
      }
    });
    const url = method === 'DELETE'
      ? withQuery(`${canvasesUrl}/${encodeURIComponent(name)}${suffix}`, { client_id: realtimeClientId })
      : `${canvasesUrl}/${encodeURIComponent(name)}${suffix}`;
    requestAcknowledged(url, {
      method,
      requestId,
      body: method === 'DELETE' ? undefined : { client_id: realtimeClientId, ...body },
    }).catch((error) => { unsubscribe(); reject(error); });
  });
}

export const requestCanvasDocuments = (args) => requestCanvasEvent({ ...args, suffix: '/documents', eventType: UI_EVENT.CANVAS_DOCUMENTS_LOADED });
export const requestCanvasData = (args) => requestCanvasEvent({ ...args, suffix: '/data', eventType: UI_EVENT.CANVAS_DATA_LOADED });
export const updateCanvasSubscription = (args) => requestCanvasEvent({ ...args, suffix: '/subscription', method: 'PUT', eventType: UI_EVENT.CANVAS_SUBSCRIPTION_UPDATED });
export const clearCanvasSubscription = (args) => requestCanvasEvent({ ...args, suffix: '/subscription', method: 'DELETE', eventType: UI_EVENT.CANVAS_SUBSCRIPTION_UPDATED });

function applySnapshot(state, payload) {
  state.canvases.items = Array.isArray(payload.items) ? payload.items : [];
  state.canvases.loaded = true;
  if (!state.canvases.loading || payload.request_id === state.canvases.requestId) {
    state.canvases.loading = false;
    state.canvases.error = null;
    state.canvases.requestId = null;
  }
}

export const eventHandlers = {
  [UI_EVENT.CANVASES_SNAPSHOT]: applySnapshot,
  [UI_EVENT.CANVAS_DELETED]: (state, payload) => {
    state.canvases.items = state.canvases.items.filter((item) => item.name !== payload.name);
    delete state.canvases.filesByName[payload.name];
  },
  [UI_EVENT.CANVAS_FILES_LOADED]: (state, payload) => {
    state.canvases.filesByName[payload.name] = { revision: payload.revision, files: payload.files || {} };
    completeKeyedRequest(state.canvases.filesRequestByName, payload.name, payload.request_id);
  },
};

export function buildExtraReducers(builder) {
  addRequestLifecycle(builder, fetchCanvases, { select: (state) => state.canvases, errorMessage: 'Failed to load canvases' });
  return addKeyedRequestLifecycle(builder, fetchCanvasFiles, { selectRequests: (state) => state.canvases.filesRequestByName, keyFromArgs: ({ name } = {}) => name, errorMessage: 'Failed to load canvas files' });
}

export const selectCanvases = (state) => state.uiData.canvases.items;
export const selectCanvasesState = (state) => state.uiData.canvases;
export const selectCanvas = (state, name) => selectCanvases(state).find((item) => item.name === name) || null;
export const selectCanvasFiles = (state, name) => state.uiData.canvases.filesByName[name] || null;
export const selectCanvasFilesRequest = (state, name) => selectKeyedRequest(state.uiData.canvases.filesRequestByName, name);
