/**
 * Logs domain: result pages, source metadata, and export chunks are hydrated
 * exclusively from canonical `/api/ui/stream` events.
 */

const MAX_COMPLETED_EXPORTS = 10;

function pruneCompletedExports(state, keepRequestId = null) {
  const completedRequestIds = Object.entries(state.logs.exportsByRequestId)
    .filter(([requestId, exportState]) => exportState.completed && requestId !== keepRequestId)
    .map(([requestId]) => requestId);
  const completedCount = Object.values(state.logs.exportsByRequestId)
    .filter((exportState) => exportState.completed)
    .length;
  const excessCount = completedCount - MAX_COMPLETED_EXPORTS;
  if (excessCount > 0) {
    for (const requestId of completedRequestIds.slice(0, excessCount)) {
      delete state.logs.exportsByRequestId[requestId];
    }
  }
}

export const initialState = {
  logs: {
    page: null,
    sourceGroups: [],
    folders: [],
    requestId: null,
    filters: null,
    activeSelection: null,
    dateRange: null,
    cursor: null,
    loaded: false,
    error: null,
    exportsByRequestId: {},
  },
};

function exportStateFor(state, requestId, payload = {}) {
  if (!requestId) {
    return null;
  }
  if (!state.logs.exportsByRequestId[requestId]) {
    state.logs.exportsByRequestId[requestId] = {
      format: payload.format || 'text',
      total: Number.isFinite(payload.total) ? payload.total : 0,
      chunks: [],
      completed: false,
    };
  }
  return state.logs.exportsByRequestId[requestId];
}

export const eventHandlers = {
  'logs.page.loaded': (state, payload) => {
    state.logs.page = payload;
    state.logs.requestId = payload.request_id || null;
    state.logs.loaded = true;
    state.logs.error = null;
    state.logs.filters = payload.filters || null;
    state.logs.activeSelection = payload.active_selection || null;
    state.logs.dateRange = payload.date_range || null;
    state.logs.cursor = payload.cursor || null;
    if (Array.isArray(payload.source_groups)) {
      state.logs.sourceGroups = payload.source_groups;
    }
    if (Array.isArray(payload.folders)) {
      state.logs.folders = payload.folders;
    }
  },
  'logs.sources.loaded': (state, payload) => {
    state.logs.sourceGroups = Array.isArray(payload.source_groups) ? payload.source_groups : [];
    state.logs.folders = Array.isArray(payload.folders) ? payload.folders : [];
  },
  'logs.export.started': (state, payload) => {
    const requestId = payload.request_id;
    if (!requestId) {
      return;
    }
    state.logs.exportsByRequestId[requestId] = {
      format: payload.format || 'text',
      total: Number.isFinite(payload.total) ? payload.total : 0,
      chunks: [],
      completed: false,
    };
  },
  'logs.export.chunk': (state, payload) => {
    const exportState = exportStateFor(state, payload.request_id, payload);
    if (!exportState || !Number.isInteger(payload.index) || payload.index < 0) {
      return;
    }
    exportState.chunks[payload.index] = Array.isArray(payload.entries) ? payload.entries : [];
  },
  'logs.export.completed': (state, payload) => {
    const exportState = exportStateFor(state, payload.request_id, payload);
    if (exportState) {
      exportState.completed = true;
      pruneCompletedExports(state, payload.request_id);
    }
  },
};

export const selectLogsPage = (state) => state.uiData.logs.page;
export const selectLogsState = (state) => state.uiData.logs;
export const selectLogsExport = (state, requestId) => (
  requestId ? state.uiData.logs.exportsByRequestId[requestId] || null : null
);
