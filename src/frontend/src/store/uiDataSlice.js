/**
 * Assemble feature-owned state into the stable state.uiData tree.
 * Features export initialState, reducers, eventHandlers and optional
 * buildExtraReducers; transport and feature UI do not depend on this composer.
 */
import { createSelector, createSlice } from '@reduxjs/toolkit';
import { addKeyedStreamFailureLifecycle } from './async';
import * as chats from '../features/chat/state/chats';
import * as agents from '../features/agents/state/agents';
import * as tasks from '../features/tasks/state';
import * as knowledge from '../features/knowledge/state';
import * as instructions from '../features/instructions/state';
import * as mcp from '../features/settings/state/mcp';
import * as status from '../features/monitoring/state';
import * as alerts from '../features/alerts/state';
import * as widgets from '../features/schedules/widgetsState';
import * as schedules from '../features/schedules/state';
import * as inbox from '../features/inbox/state';
import * as workflows from '../features/workflows/state/workflows';
import * as logs from '../features/monitoring/logsState';
import * as preferences from '../features/settings/state/preferences';
import * as marketplaces from '../features/plugins/state';
import * as repos from '../features/repositories/state';
import * as policies from '../features/settings/state/policies';
import * as documentTypes from '../features/documents/state';
import * as modelProfiles from '../features/settings/state/modelProfiles';
import * as providers from '../features/settings/state/providers';
import * as modules from '../features/modules/state';

import * as search from '../features/search/state';
import * as insights from '../features/insights/state';

const domains = [status, alerts, widgets, schedules, mcp, knowledge, instructions, tasks, workflows, agents, chats, inbox, logs, preferences, marketplaces, repos, documentTypes, policies, modelProfiles, providers, modules, search, insights];

const initialState = domains.reduce(
  (acc, domain) => ({ ...acc, ...domain.initialState }),
  {},
);

// Domain event-handler maps merged into one lookup keyed by realtime event type.
const eventHandlers = new Map();
for (const domain of domains) {
  for (const [type, handler] of Object.entries(domain.eventHandlers || {})) {
    const handlers = eventHandlers.get(type) || [];
    handlers.push(handler);
    eventHandlers.set(type, handlers);
  }
}

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
  'modules.snapshot': '/modules',
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
      for (const handler of eventHandlers.get(event?.type) || []) {
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

export const selectUnseenNavRoutes = (state) => state.uiData.navActivity.unseenRoutes;
export const selectHasUnseenNavRoute = createSelector(
  [selectUnseenNavRoutes, (_state, route) => route],
  (unseenRoutes, route) => Boolean(route && unseenRoutes[route]),
);

export default uiDataSlice.reducer;
