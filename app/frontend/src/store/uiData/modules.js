/** Module catalogue, file and revision state, backed by the canonical UI stream. */
import {
  addKeyedRequestLifecycle,
  addRequestLifecycle,
  completeKeyedRequest,
  createAcknowledgedThunk,
  requestAcknowledged,
  selectKeyedRequest,
} from './async';
import { withQuery, createOperationRequestId } from '../http';
import { realtimeClientId, waitForRealtimeClient } from '../realtimeClient';
import { subscribeUiEvent } from '../realtimeEvents';

export const initialState = {
  modules: {
    items: [], loaded: false, loading: false, error: null, requestId: null,
    filesByName: {}, revisionsByName: {}, revisionByKey: {},
    filesRequestByName: {}, revisionsRequestByName: {}, revisionRequestByKey: {},
  },
};

const revisionKey = (name, id) => `${name}@${id}`;

export const fetchModules = createAcknowledgedThunk(
  'uiData/fetchModules',
  async ({ modulesUrl }, { requestId }) => requestAcknowledged(modulesUrl, { requestId }),
);
export const fetchModuleFiles = createAcknowledgedThunk(
  'uiData/fetchModuleFiles',
  async ({ modulesUrl, name }, { requestId }) => {
    await waitForRealtimeClient();
    return requestAcknowledged(withQuery(`${modulesUrl}/${encodeURIComponent(name)}/files`, { client_id: realtimeClientId }), { requestId });
  },
);
export const fetchModuleRevisions = createAcknowledgedThunk(
  'uiData/fetchModuleRevisions',
  async ({ modulesUrl, name }, { requestId }) => {
    await waitForRealtimeClient();
    return requestAcknowledged(withQuery(`${modulesUrl}/${encodeURIComponent(name)}/revisions`, { client_id: realtimeClientId }), { requestId });
  },
);
export const fetchModuleRevision = createAcknowledgedThunk(
  'uiData/fetchModuleRevision',
  async ({ modulesUrl, name, id }, { requestId }) => {
    await waitForRealtimeClient();
    return requestAcknowledged(withQuery(`${modulesUrl}/${encodeURIComponent(name)}/revisions/${encodeURIComponent(id)}`, { client_id: realtimeClientId }), { requestId });
  },
);
export const restoreModuleRevision = createAcknowledgedThunk(
  'uiData/restoreModuleRevision',
  async ({ modulesUrl, name, capturedAt }, { requestId }) => {
    await waitForRealtimeClient();
    return requestAcknowledged(`${modulesUrl}/${encodeURIComponent(name)}/restore`, {
      method: 'POST', requestId, body: { client_id: realtimeClientId, captured_at: capturedAt },
    });
  },
);
export const deleteModule = createAcknowledgedThunk(
  'uiData/deleteModule',
  async ({ modulesUrl, name }, { requestId }) => {
    await waitForRealtimeClient();
    return requestAcknowledged(withQuery(`${modulesUrl}/${encodeURIComponent(name)}`, { client_id: realtimeClientId }), { method: 'DELETE', requestId });
  },
);
export const reportModuleStatus = createAcknowledgedThunk(
  'uiData/reportModuleStatus',
  async ({ modulesUrl, name, state, revision, errors }, { requestId }) => {
    await waitForRealtimeClient();
    return requestAcknowledged(`${modulesUrl}/${encodeURIComponent(name)}/status`, {
      method: 'POST', requestId, body: { client_id: realtimeClientId, state, revision, errors },
    });
  },
);

/** Submit a targeted bridge request and resolve only its matching stream response. */
export async function requestModuleEvent({ modulesUrl, name, suffix, method = 'POST', body, eventType }) {
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
      ? withQuery(`${modulesUrl}/${encodeURIComponent(name)}${suffix}`, { client_id: realtimeClientId })
      : `${modulesUrl}/${encodeURIComponent(name)}${suffix}`;
    requestAcknowledged(url, {
      method,
      requestId,
      body: method === 'DELETE' ? undefined : { client_id: realtimeClientId, ...body },
    }).catch((error) => { unsubscribe(); reject(error); });
  });
}

export const requestModuleDocuments = (args) => requestModuleEvent({ ...args, suffix: '/documents', eventType: 'module.documents.loaded' });
export const requestModuleData = (args) => requestModuleEvent({ ...args, suffix: '/data', eventType: 'module.data.loaded' });
export const updateModuleSubscription = (args) => requestModuleEvent({ ...args, suffix: '/subscription', method: 'PUT', eventType: 'module.subscription.updated' });
export const clearModuleSubscription = (args) => requestModuleEvent({ ...args, suffix: '/subscription', method: 'DELETE', eventType: 'module.subscription.updated' });

function applySnapshot(state, payload) {
  state.modules.items = Array.isArray(payload.items) ? payload.items : [];
  state.modules.loaded = true;
  if (!state.modules.loading || payload.request_id === state.modules.requestId) {
    state.modules.loading = false;
    state.modules.error = null;
    state.modules.requestId = null;
  }
}

export const eventHandlers = {
  'modules.snapshot': applySnapshot,
  'module.deleted': (state, payload) => {
    state.modules.items = state.modules.items.filter((item) => item.name !== payload.name);
    delete state.modules.filesByName[payload.name];
    delete state.modules.revisionsByName[payload.name];
  },
  'module.files.loaded': (state, payload) => {
    state.modules.filesByName[payload.name] = { revision: payload.revision, files: payload.files || {} };
    completeKeyedRequest(state.modules.filesRequestByName, payload.name, payload.request_id);
  },
  'module.revisions.loaded': (state, payload) => {
    state.modules.revisionsByName[payload.name] = Array.isArray(payload.revisions) ? payload.revisions : [];
    completeKeyedRequest(state.modules.revisionsRequestByName, payload.name, payload.request_id);
  },
  'module.revision.loaded': (state, payload) => {
    const key = revisionKey(payload.name, payload.revision?.id);
    state.modules.revisionByKey[key] = payload.revision || null;
    completeKeyedRequest(state.modules.revisionRequestByKey, key, payload.request_id);
  },
  'module.restored': (state, payload) => {
    const item = state.modules.items.find((candidate) => candidate.name === payload.name);
    if (item) item.revision = payload.revision;
  },
};

export function buildExtraReducers(builder) {
  addRequestLifecycle(builder, fetchModules, { select: (state) => state.modules, errorMessage: 'Failed to load modules' });
  addKeyedRequestLifecycle(builder, fetchModuleFiles, { selectRequests: (state) => state.modules.filesRequestByName, keyFromArgs: ({ name } = {}) => name, errorMessage: 'Failed to load module files' });
  addKeyedRequestLifecycle(builder, fetchModuleRevisions, { selectRequests: (state) => state.modules.revisionsRequestByName, keyFromArgs: ({ name } = {}) => name, errorMessage: 'Failed to load module revisions' });
  return addKeyedRequestLifecycle(builder, fetchModuleRevision, { selectRequests: (state) => state.modules.revisionRequestByKey, keyFromArgs: ({ name, id } = {}) => name && id ? revisionKey(name, id) : null, errorMessage: 'Failed to load module revision' });
}

export const selectModules = (state) => state.uiData.modules.items;
export const selectModulesState = (state) => state.uiData.modules;
export const selectModule = (state, name) => selectModules(state).find((item) => item.name === name) || null;
export const selectModuleFiles = (state, name) => state.uiData.modules.filesByName[name] || null;
export const selectModuleRevisions = (state, name) => state.uiData.modules.revisionsByName[name] || [];
export const selectModuleRevision = (state, name, id) => state.uiData.modules.revisionByKey[revisionKey(name, id)] || null;
export const selectModuleFilesRequest = (state, name) => selectKeyedRequest(state.uiData.modules.filesRequestByName, name);
export const selectModuleRevisionsRequest = (state, name) => selectKeyedRequest(state.uiData.modules.revisionsRequestByName, name);
export const selectModuleRevisionRequest = (state, name, id) => selectKeyedRequest(state.uiData.modules.revisionRequestByKey, name && id ? revisionKey(name, id) : null);
