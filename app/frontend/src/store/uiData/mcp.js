/**
 * MCP domain: external MCP servers, kept live by `mcp.snapshot` events on
 * /api/ui/stream and seeded via REST when the Tools page mounts.
 */
import { createAsyncThunk } from '@reduxjs/toolkit';
import { requestJson } from '../http';

export const initialState = {
  mcp: {
    servers: [],
    loaded: false,
    loading: false,
    error: null,
  },
};

// Acknowledge an MCP-list request; the snapshot arrives on the realtime stream
// as `mcp.snapshot` (the GET just triggers the broadcast).
export const fetchMcp = createAsyncThunk(
  'uiData/fetchMcp',
  ({ mcpUrl }) => requestJson(mcpUrl, { errorLabel: 'Failed to load MCP servers' }),
);

export const eventHandlers = {
  'mcp.snapshot': (state, payload) => {
    state.mcp.servers = Array.isArray(payload.items) ? payload.items : [];
    state.mcp.loaded = true;
    state.mcp.loading = false;
    state.mcp.error = null;
  },
};

export function buildExtraReducers(builder) {
  builder
    .addCase(fetchMcp.pending, (state) => {
      state.mcp.loading = true;
      state.mcp.error = null;
    })
    .addCase(fetchMcp.fulfilled, (state) => {
      state.mcp.loading = false;
    })
    .addCase(fetchMcp.rejected, (state, action) => {
      state.mcp.loading = false;
      state.mcp.error = action.error?.message || 'Failed to load MCP servers';
    });
}

export const selectMcpServers = (state) => state.uiData.mcp.servers;
export const selectMcpState = (state) => state.uiData.mcp;
