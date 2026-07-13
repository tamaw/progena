/**
 * MCP domain: external MCP servers, kept live by `mcp.snapshot` events on
 * /api/ui/stream and seeded via REST when the Tools page mounts.
 */
import { createAcknowledgedThunk, requestAcknowledged, addRequestLifecycle } from './async';
export const initialState = {
  mcp: {
    servers: [],
    loaded: false,
    loading: false,
    error: null,
    requestId: null,
  },
};

// Acknowledge an MCP-list request; the snapshot arrives on the realtime stream
// as `mcp.snapshot` (the GET just triggers the broadcast).
export const fetchMcp = createAcknowledgedThunk('uiData/fetchMcp', async ({ mcpUrl }, { requestId }) => {
  return requestAcknowledged(mcpUrl, { requestId });
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
};

export function buildExtraReducers(builder) {
  return addRequestLifecycle(builder, fetchMcp, {
    select: (state) => state.mcp,
    errorMessage: 'Failed to load MCP servers',
  });
}

export const selectMcpServers = (state) => state.uiData.mcp.servers;
export const selectMcpState = (state) => state.uiData.mcp;
