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
import { createSelector, createSlice } from '@reduxjs/toolkit';
import * as chats from './uiData/chats';
import * as agents from './uiData/agents';
import * as tasks from './uiData/tasks';
import * as knowledge from './uiData/knowledge';
import * as prompts from './uiData/prompts';
import * as mcp from './uiData/mcp';
import * as runtime from './uiData/runtime';
import * as inbox from './uiData/inbox';
import * as workflows from './uiData/workflows';
import * as logs from './uiData/logs';
import * as preferences from './uiData/preferences';
import * as marketplaces from './uiData/marketplaces';
import * as repos from './uiData/repos';

const domains = [runtime, mcp, knowledge, prompts, tasks, workflows, agents, chats, inbox, logs, preferences, marketplaces, repos];

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

// Nav "unseen activity" tracking: cross-cutting (spans every domain's events),
// so it lives here rather than in one domain module - a domain-owned entry
// here would collide with that event type's real handler in the merged
// `eventHandlers` map above. Maps a realtime event type to the nav route that
// owns it; only pages with a real live-update signal are tracked (Chat/Inbox
// already have their own precise unread tracking).
const NAV_ACTIVITY_EVENT_ROUTES = {
  'widgets.updated': '/dashboard',
  'kanban.snapshot': '/tasks',
  'task.content.updated': '/tasks',
  'task.activity.updated': '/tasks',
  'agents.snapshot': '/agents',
  'agent.upserted': '/agents',
  'plugins.snapshot': '/plugins',
  'mcp.snapshot': '/tools',
  'knowledge.snapshot': '/knowledge',
  'schedule.snapshot': '/schedules',
  'workflows.snapshot': '/workflows',
  'workflow.updated': '/workflows',
  'workflow.deleted': '/workflows',
  'workflow.runs.snapshot': '/workflows',
  'workflow.run.started': '/workflows',
  'workflow.run.updated': '/workflows',
  'workflow.run.completed': '/workflows',
  'workflow.run.failed': '/workflows',
  'repositories.snapshot': '/repositories',
};

const navActivityInitialState = {
  navActivity: {
    activeRoute: null,
    // A route's first-ever event this session is its baseline (e.g. the
    // initial snapshot burst on connect, or a page's own first fetch) rather
    // than "new" activity; only later events light the dot.
    seenBaselineRoutes: {},
    unseenRoutes: {},
  },
};

const uiDataSlice = createSlice({
  name: 'uiData',
  initialState: { ...initialState, ...navActivityInitialState },
  reducers: {
    ...domainReducers,
    applyUiEvent(state, action) {
      const event = action.payload;
      const handler = eventHandlers[event?.type];
      if (handler) {
        handler(state, event?.payload || {}, event);
      }
      const route = NAV_ACTIVITY_EVENT_ROUTES[event?.type];
      if (route) {
        if (!state.navActivity.seenBaselineRoutes[route]) {
          state.navActivity.seenBaselineRoutes[route] = true;
        } else if (state.navActivity.activeRoute !== route) {
          state.navActivity.unseenRoutes[route] = true;
        }
      }
    },
    setActiveNavRoute(state, action) {
      const route = action.payload;
      state.navActivity.activeRoute = route;
      if (route) {
        state.navActivity.seenBaselineRoutes[route] = true;
        delete state.navActivity.unseenRoutes[route];
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
  setActiveNavRoute,
  setErroredChatIds,
  setGeneratingChatIds,
  taskActivityReceived,
  taskContentReceived,
  setUnreadChatIds,
  touchChatSession,
} = uiDataSlice.actions;

export const selectUnseenNavRoutes = (state) => state.uiData.navActivity.unseenRoutes;
export const selectHasUnseenNavRoute = createSelector(
  [selectUnseenNavRoutes, (_state, route) => route],
  (unseenRoutes, route) => Boolean(route && unseenRoutes[route]),
);

// Re-export thunks + selectors from their domain modules.
export {
  fetchChatSessions,
  fetchChatMessages,
  requestChatSessionDetail,
  requestChatSessionReconciliation,
  createChatSession,
  ensureDraftChatSession,
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
  selectChatReconciliationState,
  selectRequestIdByThreadId,
  selectRequestIdForThread,
} from './uiData/chats';

export {
  fetchAgentProfiles,
  requestAgentDetail,
  updateAgentGraph,
  deleteAgentProfile,
  updateObjectAgentProfile,
  fetchAgentSkillContent,
  updateAgentSkillContent,
  fetchRoutineRuns,
  selectAgentProfiles,
  selectAgentProfilesState,
  selectActiveAgentProfile,
  selectActiveAgentProfileState,
  selectAgentSkillContent,
  selectAgentRoutineRuns,
} from './uiData/agents';

export {
  fetchKanbanBoard,
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
  selectKnowledgeSearch,
} from './uiData/knowledge';

export {
  fetchPrompts,
  savePrompt,
  fetchPromptContent,
  selectPrompts,
  selectPromptsState,
  selectPromptContentEntry,
} from './uiData/prompts';

export {
  fetchMcp,
  fetchMcpServerDetail,
  selectMcpServers,
  selectMcpState,
  selectMcpServerDetail,
} from './uiData/mcp';

export {
  fetchWidgets,
  markAlertsViewed,
  selectUiStatus,
  selectUiAlerts,
  selectUiWidgets,
  selectSchedules,
  selectLastReceivedAlert,
} from './uiData/runtime';
export {
  selectLogsPage,
  selectLogsState,
  selectLogsExport,
} from './uiData/logs';

export {
  fetchInbox,
  markInboxMessageRead,
  deleteInboxMessage,
  decideInboxMessage,
  archiveInboxMessage,
  selectInboxMessages,
  selectInboxState,
  selectInboxUnreadCount,
} from './uiData/inbox';

export {
  fetchWorkflows,
  fetchWorkflowContent,
  saveWorkflow,
  createWorkflow,
  deleteWorkflow,
  pauseWorkflow,
  resumeWorkflow,
  triggerWorkflow,
  fetchWorkflowRuns,
  selectWorkflows,
  selectWorkflowsState,
  selectWorkflowRuns,
  selectWorkflowDetailByPath,
  selectWorkflowLatestRunById,
} from './uiData/workflows';

export {
  fetchPreferences,
  savePreferences,
  selectPreferences,
  selectPreferencesSaveRequest,
} from './uiData/preferences';

export {
  fetchMarketplaces,
  saveMarketplaces,
  fetchPlugins,
  fetchMarketplaceCatalog,
  installPlugin,
  uninstallPlugin,
  fetchPluginSkillContent,
  selectMarketplaces,
  selectMarketplacesState,
  selectPlugins,
  selectPluginsState,
  selectCatalogByMarketplace,
  selectMarketplaceCatalog,
  selectPluginSkillContent,
} from './uiData/marketplaces';

export {
  fetchRepos,
  fetchRepoDetail,
  createRepo,
  updateRepo,
  deleteRepo,
  fetchRepoUpstream,
  selectRepos,
  selectReposState,
  selectWorkspaceSignal,
  selectRepoDetail,
} from './uiData/repos';

export default uiDataSlice.reducer;
