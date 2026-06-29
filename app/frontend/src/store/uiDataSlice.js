/**
 * uiData slice — composer + public barrel.
 *
 * The slice is split into one module per domain under `./uiData/*`. Each domain
 * owns its `initialState` fragment, async thunks, synchronous `reducers`, an
 * `eventHandlers` map (realtime event type -> draft mutator), a
 * `buildExtraReducers(builder)` registrar, and its selectors.
 *
 * This file assembles those modules into a single `uiData` slice (so the store
 * shape stays `state.uiData.*`) and re-exports every thunk, action, and selector
 * so existing imports from `../store/uiDataSlice` keep working unchanged.
 *
 * To add a realtime event: add an entry to the owning domain's `eventHandlers`.
 * To add a domain: create `./uiData/<name>.js` and register it in the arrays
 * below.
 */
import { createSlice } from '@reduxjs/toolkit';
import * as chats from './uiData/chats';
import * as agents from './uiData/agents';
import * as tasks from './uiData/tasks';
import * as knowledge from './uiData/knowledge';
import * as mcp from './uiData/mcp';
import * as runtime from './uiData/runtime';

const domains = [runtime, mcp, knowledge, tasks, agents, chats];

const initialState = domains.reduce(
  (acc, domain) => ({ ...acc, ...domain.initialState }),
  {},
);

// Domain event-handler maps merged into one lookup keyed by realtime event type.
const eventHandlers = domains.reduce(
  (acc, domain) => ({ ...acc, ...(domain.eventHandlers || {}) }),
  {},
);

const domainReducers = domains.reduce(
  (acc, domain) => ({ ...acc, ...(domain.reducers || {}) }),
  {},
);

const uiDataSlice = createSlice({
  name: 'uiData',
  initialState,
  reducers: {
    ...domainReducers,
    applyUiEvent(state, action) {
      const event = action.payload;
      const handler = eventHandlers[event?.type];
      if (handler) {
        handler(state, event?.payload || {}, event);
      }
    },
  },
  extraReducers: (builder) => {
    for (const domain of domains) {
      domain.buildExtraReducers?.(builder);
    }
  },
});

export const {
  applyUiEvent,
  clearActiveChatSession,
  clearChatErrored,
  clearChatGenerating,
  clearChatUnread,
  kanbanSnapshotReceived,
  markChatErrored,
  markChatGenerating,
  markChatUnread,
  scheduleSnapshotReceived,
  setErroredChatIds,
  setGeneratingChatIds,
  taskActivityReceived,
  taskContentReceived,
  setUnreadChatIds,
} = uiDataSlice.actions;

// Re-export thunks + selectors from their domain modules.
export {
  fetchChatSessions,
  requestChatSessionDetail,
  createChatSession,
  updateChatSessionMetadata,
  deleteChatSession,
  selectChatSessions,
  selectChatSessionsState,
  selectUnreadChatIds,
  selectGeneratingChatIds,
  selectErroredChatIds,
  selectChatIndicators,
  selectActiveChatSession,
  selectActiveChatSessionState,
} from './uiData/chats';

export {
  fetchAgentProfiles,
  requestAgentDetail,
  updateAgentGraph,
  selectAgentProfiles,
  selectAgentProfilesState,
  selectActiveAgentProfile,
  selectActiveAgentProfileState,
} from './uiData/agents';

export {
  fetchKanbanBoard,
  fetchTaskContent,
  fetchTaskActivity,
  selectKanbanBoard,
  selectTasksState,
  selectTaskContentEntry,
  selectTaskActivityEntry,
} from './uiData/tasks';

export {
  fetchKnowledge,
  saveKnowledge,
  indexKnowledgeSource,
  selectKnowledgeBases,
  selectKnowledgeState,
} from './uiData/knowledge';

export {
  fetchMcp,
  selectMcpServers,
  selectMcpState,
} from './uiData/mcp';

export {
  fetchWidgets,
  selectUiStatus,
  selectUiAlerts,
  selectUiWidgets,
  selectSchedules,
} from './uiData/runtime';

export default uiDataSlice.reducer;
