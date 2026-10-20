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
import { addKeyedStreamFailureLifecycle } from './uiData/async';
import * as chats from './uiData/chats';
import * as agents from './uiData/agents';
import * as tasks from './uiData/tasks';
import * as knowledge from './uiData/knowledge';
import * as instructions from './uiData/instructions';
import * as mcp from './uiData/mcp';
import * as runtime from './uiData/runtime';
import * as inbox from './uiData/inbox';
import * as workflows from './uiData/workflows';
import * as logs from './uiData/logs';
import * as preferences from './uiData/preferences';
import * as marketplaces from './uiData/marketplaces';
import * as repos from './uiData/repos';
import * as policies from './uiData/policies';
import * as documentTypes from './uiData/documentTypes';
import * as modelProfiles from './uiData/modelProfiles';

import * as search from './uiData/search';

const domains = [runtime, mcp, knowledge, instructions, tasks, workflows, agents, chats, inbox, logs, preferences, marketplaces, repos, documentTypes, policies, modelProfiles, search];

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
  'instructions.snapshot': '/instructions',
  'agent.upserted': '/agents',
  'agent.routine.run.started': '/routines',
  'agent.routine.run.completed': '/routines',
  'agent.routine.run.failed': '/routines',
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
  'profiles.snapshot': '/profiles',
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
      // Reconnect and other tabs hydrate this shared catalogue globally.
      // Identical snapshots are not new profile activity.
      const profileChanged = event?.type !== 'profiles.snapshot'
        || JSON.stringify(state.modelProfiles.profiles) !== JSON.stringify(event?.payload?.profiles || []);
      const handler = eventHandlers[event?.type];
      if (handler) {
        handler(state, event?.payload || {}, event);
      }
      const route = NAV_ACTIVITY_EVENT_ROUTES[event?.type];
      if (route) {
        if (!state.navActivity.seenBaselineRoutes[route]) {
          state.navActivity.seenBaselineRoutes[route] = true;
        } else if (state.navActivity.activeRoute !== route && profileChanged) {
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
    addKeyedStreamFailureLifecycle(builder);
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
  fetchRoutineRevisions,
  fetchRoutineRevision,
  fetchRunState,
  startRoutineRun,
  answerRoutineApproval,
  selectAgentProfiles,
  selectAgentProfilesState,
  selectActiveAgentProfile,
  selectActiveAgentProfileState,
  selectAgentSkillContent,
  selectAgentRoutineRuns,
  selectAgentRoutineRunsHasMore,
  selectAgentRoutineRevisions,
  selectAgentRoutineRevisionContent,
  selectAgentRunState,
  selectRoutineRunLifecycle,
  selectLatestRoutineRunLifecycle,
  selectRoutineLatestRunStatusByAgentId,
  selectLastRoutineRunNotification,
} from './uiData/agents';

export {
  fetchKanbanBoard,
  fetchTaskActivity,
  selectKanbanBoard,
  selectTasksState,
  selectTaskContentEntry,
  selectTaskActivityEntry,
  selectTaskActivityRequest,
} from './uiData/tasks';

export {
  fetchKnowledge,
  saveKnowledge,
  fetchKnowledgePages,
  fetchKnowledgePage,
  fetchKnowledgePageRevisions,
  fetchKnowledgePageRevision,
  selectKnowledgeBases,
  selectKnowledgeState,
  selectKnowledgePages,
  selectKnowledgePage,
  selectKnowledgeRevisions,
  selectKnowledgeRevision,
  selectKnowledgePageRequest,
  selectKnowledgePageRevisionsRequest,
  selectKnowledgePageRevisionRequest,
} from './uiData/knowledge';

export {
  searchAll,
  selectSearchResults,
} from './uiData/search';

export {
  fetchInstructions,
  fetchInstruction,
  saveInstruction,
  fetchInstructionRevisions,
  fetchInstructionRevision,
  restoreInstructionRevision,
  selectInstructions,
  selectInstructionsState,
  selectInstruction,
  selectInstructionRevisions,
  selectInstructionRevision,
  selectInstructionContentRequest,
  selectInstructionRevisionsRequest,
  selectInstructionRevisionRequest,
} from './uiData/instructions';

export {
  fetchDocumentTypes,
  selectDocumentTypes,
  selectDocumentTypesState,
} from './uiData/documentTypes';

export {
  fetchMcp,
  fetchMcpServerDetail,
  selectMcpServers,
  selectMcpState,
  selectMcpServerDetail,
  selectMcpServerDetailRequest,
} from './uiData/mcp';

export {
  fetchWidgets,
  fetchSchedules,
  markAlertsViewed,
  selectUiStatus,
  selectUiAlerts,
  selectUiWidgets,
  selectSchedules,
  selectSchedulesState,
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
  selectWorkflowDetailRequest,
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
  selectMarketplaceCatalogRequest,
  selectPluginSkillContentRequest,
} from './uiData/marketplaces';

export {
  fetchRepos,
  fetchRepoDetail,
  updateRepo,
  deleteRepo,
  fetchRepoUpstream,
  selectRepos,
  selectReposState,
  selectWorkspaceSignal,
  selectRepoDetail,
} from './uiData/repos';

export {
  fetchModelProfiles,
  patchModelProfile,
  deleteModelProfile,
  selectModelProfiles,
  selectModelProfilesState,
} from './uiData/modelProfiles';

export default uiDataSlice.reducer;

export { fetchPolicies, savePolicy, selectPolicies } from './uiData/policies';
