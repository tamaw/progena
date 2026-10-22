/**
 * MCP domain: external MCP servers, kept live by `mcp.snapshot` events on
 * /api/ui/stream and seeded via REST when the Tools page mounts. The list
 * carries scalar fields only (enabled/transport/url/kind) — per-tool schemas
 * and log history are fetched on demand per server via `mcp.server.detail.updated`.
 */
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle, addKeyedRequestLifecycle, completeKeyedRequest, selectKeyedRequest } from '../../../store/async';
import { withQuery } from '../../../store/http';

export const initialState = {
  mcp: {
    servers: [],
    loaded: false,
    loading: false,
    error: null,
    requestId: null,
    detailById: {},
    detailRequestById: {},
  },
};

// Acknowledge an MCP-list request; the snapshot arrives on the realtime stream
// as `mcp.snapshot` (the GET just triggers the broadcast).
export const fetchMcp = createAcknowledgedThunk('uiData/fetchMcp', async ({ mcpUrl }, { requestId }) => {
  return requestAcknowledged(mcpUrl, { requestId });
});

// Fetch one server's full detail (tools + logs) on demand — the list items
// carry scalar fields only.
export const fetchMcpServerDetail = createAcknowledgedThunk('uiData/fetchMcpServerDetail', async ({ mcpUrl, id }, { requestId }) => {
  return requestAcknowledged(withQuery(`${mcpUrl}/content`, { id }), { requestId });
});

export const eventHandlers = {
  'mcp.snapshot': (state, payload) => {
    state.mcp.servers = Array.isArray(payload.items) ? payload.items : [];
    state.mcp.loaded = true;
    if (!state.mcp.loading || payload.request_id === state.mcp.requestId) {
      state.mcp.loading = false;
      state.mcp.error = null;
      state.mcp.requestId = null;
    }
  },
  'mcp.server.detail.updated': (state, payload) => {
    if (typeof payload?.id === 'string' && payload.id) {
      state.mcp.detailById[payload.id] = payload;
      completeKeyedRequest(state.mcp.detailRequestById, payload.id, payload.request_id);
    }
  },
};

export function buildExtraReducers(builder) {
  addRequestLifecycle(builder, fetchMcp, {
    select: (state) => state.mcp,
    errorMessage: 'Failed to load MCP servers',
  });
  return addKeyedRequestLifecycle(builder, fetchMcpServerDetail, {
    selectRequests: (state) => state.mcp.detailRequestById,
    keyFromArgs: ({ id } = {}) => id,
    errorMessage: 'Failed to load MCP server detail',
  });
}

export const selectMcpServers = (state) => state.uiData.mcp.servers;
export const selectMcpState = (state) => state.uiData.mcp;
export const selectMcpServerDetail = (state, id) => (
  id ? state.uiData.mcp.detailById[id] || null : null
);
export const selectMcpServerDetailRequest = (state, id) => (
  selectKeyedRequest(state.uiData.mcp.detailRequestById, id)
);
