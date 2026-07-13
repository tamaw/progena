/**
 * MCP domain: external MCP servers, kept live by `mcp.snapshot` events on
 * /api/ui/stream and seeded via REST when the Tools page mounts.
 */
import { createAsyncThunk } from '@reduxjs/toolkit';
import { createOperationRequestId, requestAcknowledged } from '../http';
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
export const fetchMcp = createAsyncThunk('uiData/fetchMcp', async ({ mcpUrl }, { requestId }) => {
  return requestAcknowledged(mcpUrl, { requestId });
}, { idGenerator: createOperationRequestId });

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
  builder
    .addCase(fetchMcp.pending, (state, action) => {
      state.mcp.loading = true;
      state.mcp.error = null;
      state.mcp.requestId = action.meta.requestId;
    })
    .addCase(fetchMcp.rejected, (state, action) => {
      if (state.mcp.requestId !== action.meta.requestId) {
        return;
      }
      state.mcp.loading = false;
      state.mcp.error = action.error?.message || 'Failed to load MCP servers';
      state.mcp.requestId = null;
    });
}

export const selectMcpServers = (state) => state.uiData.mcp.servers;
export const selectMcpState = (state) => state.uiData.mcp;
