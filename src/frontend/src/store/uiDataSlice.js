import { UI_EVENT } from '../realtime/eventVocabulary';
/**
 * Assemble feature-owned state into the stable state.uiData tree.
 * Features export initialState, reducers, eventHandlers and optional
 * buildExtraReducers; transport and feature UI do not depend on this composer.
 */
import { createSelector, createSlice } from '@reduxjs/toolkit';
import { addKeyedStreamFailureLifecycle } from './async';
import * as chats from '../features/chat/state/chats';
import * as backgroundTasks from '../features/chat/state/backgroundTasks';
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
import * as canvases from '../features/canvases/state';
import * as channels from '../features/channels/state';

import * as search from '../features/search/state';
import * as insights from '../features/insights/state';
import * as runs from '../features/runs/state';

const domains = [status, alerts, widgets, schedules, mcp, knowledge, instructions, tasks, workflows, agents, chats, backgroundTasks, inbox, logs, preferences, marketplaces, repos, documentTypes, policies, modelProfiles, providers, canvases, search, insights, runs, channels];
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

// Nav "unseen activity" tracking: the backend emits a global, route-only
// `nav.activity` when a domain event that is not a reply to one tab occurs
// (backend `NAV_ACTIVITY_ROUTES`). The domain event itself reaches only the
// tabs on that page. Chat and Inbox have their own precise unread tracking.

const navActivityInitialState = {
  navActivity: {
    activeRoute: null,
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
      for (const handler of eventHandlers.get(event?.type) || []) {
        handler(state, event?.payload || {}, event);
      }
      if (event?.type === UI_EVENT.NAV_ACTIVITY) {
        const route = event.payload?.route;
        if (route) {
          if (state.navActivity.activeRoute !== route) {
            state.navActivity.unseenRoutes[route] = true;
          }
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
