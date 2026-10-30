import { Component, createElement, Suspense, lazy } from 'react';
import { Routes, Route, Navigate, useParams } from 'react-router-dom';
import AppRuntimeShell from './shell/AppRuntimeShell';
import { localDesignLibraryEnabled } from './lib/localDesignLibrary';
import InlineFailure from './components/InlineFailure';
import EditorNavigationBoundary from './shell/EditorNavigationBoundary';

const ConversationPage = lazy(() => import('./features/chat/pages/ConversationPage'));

const InboxPage = lazy(() => import('./features/inbox/InboxPage'));

const DashboardPage = lazy(() => import('./features/dashboard/DashboardPage'));

const InsightsPage = lazy(() => import('./features/insights/InsightsPage'));

const TasksPage = lazy(() => import('./features/tasks/TasksPage'));

const KanbanPage = lazy(() => import('./features/tasks/KanbanPage'));

const LogsPage = lazy(() => import('./features/monitoring/LogsPage'));

const InstructionsPage = lazy(() => import('./features/instructions/InstructionsPage'));

const PluginsPage = lazy(() => import('./features/plugins/PluginsPage'));

const SessionsPage = lazy(() => import('./features/chat/pages/SessionsPage'));

const RepositoriesPage = lazy(() => import('./features/repositories/RepositoriesPage'));

const KnowledgePage = lazy(() => import('./features/knowledge/KnowledgePage'));

const ToolsPage = lazy(() => import('./features/settings/ToolsPage'));

const SchedulesPage = lazy(() => import('./features/schedules/SchedulesPage'));

const ProjectPage = lazy(() => import('./features/projects/ProjectPage'));

const ModelsPage = lazy(() => import('./features/settings/ModelsPage'));

const ProfilesPage = lazy(() => import('./features/settings/ProfilesPage'));

const ChannelsPage = lazy(() => import('./features/channels/ChannelsPage'));

const WorkflowsPage = lazy(() => import('./features/workflows/WorkflowsPage'));

const AgentsPage = lazy(() => import('./features/agents/AgentsPage'));

const RoutinesPage = lazy(() => import('./features/routines/RoutinesPage'));

const CanvasesPage = lazy(() => import('./features/canvases/CanvasesPage'));

const RunsPage = lazy(() => import('./features/runs/RunsPage'));

// Keep the import behind the compile-time flag so the catalogue is absent from production assets.
const DesignLibraryPage = import.meta.env.DEV
  ? lazy(() => import('./features/design-library/DesignLibraryPage'))
  : null;

class RouteErrorBoundary extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error) {
    console.error('Route failed to render:', error);
  }

  render() {
    if (this.state.error) {
      return <div className="chrome-route-error"><InlineFailure failure={this.state.error} operation="Load page" onRetry={() => window.location.reload()} /></div>;
    }
    return this.props.children;
  }
}

function SequencesRedirect() {
  const { agentId } = useParams();
  return <Navigate to={`/routines/${agentId ?? ''}`} replace />;
}

function DeferredRoute({ Page, fallback, ...pageProps }) {
  return (
    <EditorNavigationBoundary>
      <Suspense fallback={<div className="chrome-route-loading">{fallback}</div>}>
        <RouteErrorBoundary>
          {createElement(Page, pageProps)}
        </RouteErrorBoundary>
      </Suspense>
    </EditorNavigationBoundary>
  );
}

export function AppRoutes() {
  return (
<Routes>
                  <Route element={<AppRuntimeShell />}>
                    <Route path="/" element={<Navigate to="/chat" replace />} />
                    <Route path="/chat/:threadId?/:titleSlug?" element={<DeferredRoute Page={ConversationPage} fallback="Loading chat..." />} />
                    <Route path="/inbox/:messageId?/:titleSlug?" element={<DeferredRoute Page={InboxPage} fallback="Loading inbox..." />} />
                    <Route path="/dashboard" element={<DeferredRoute Page={DashboardPage} fallback="Loading dashboard..." />} />
                    <Route path="/insights" element={<DeferredRoute Page={InsightsPage} fallback="Loading insights..." />} />
                    <Route path="/runs" element={<DeferredRoute Page={RunsPage} fallback="Loading runs..." />} />
                    <Route path="/models" element={<DeferredRoute Page={ModelsPage} fallback="Loading models..." />} />
                    <Route path="/profiles/:profileName?/:titleSlug?" element={<DeferredRoute Page={ProfilesPage} fallback="Loading profiles..." />} />
                    <Route path="/channels" element={<DeferredRoute Page={ChannelsPage} fallback="Loading channels..." />} />
                    <Route path="/agents/:agentId?/:tab?" element={<DeferredRoute Page={AgentsPage} fallback="Loading agents..." />} />
                    <Route path="/routines/:agentId?" element={<DeferredRoute Page={RoutinesPage} fallback="Loading routines..." />} />
                    <Route path="/sequences/:agentId?" element={<SequencesRedirect />} />
                    <Route path="/tools/:serverId?/:titleSlug?" element={<DeferredRoute Page={ToolsPage} fallback="Loading tools..." />} />
                    <Route path="/tasks/:cardId?/:titleSlug?" element={<DeferredRoute Page={TasksPage} fallback="Loading tasks..." />} />
                    <Route path="/projects" element={<Navigate to="/solutions/main/projects/backend-runtime" replace />} />
                    <Route path="/kanban" element={<DeferredRoute Page={KanbanPage} fallback="Loading kanban..." />} />
                    <Route path="/solutions/:solutionId/projects/:projectId" element={<DeferredRoute Page={ProjectPage} fallback="Loading project..." />} />
                    <Route path="/logs" element={<DeferredRoute Page={LogsPage} fallback="Loading logs..." />} />
                    <Route path="/plugins/:marketplace?/:plugin?" element={<DeferredRoute Page={PluginsPage} fallback="Loading plugins..." />} />
                    <Route path="/instructions/*" element={<DeferredRoute Page={InstructionsPage} fallback="Loading instructions..." />} />
                    <Route path="/sessions" element={<DeferredRoute Page={SessionsPage} fallback="Loading sessions..." />} />
                    <Route path="/schedules/:scheduleId?/:titleSlug?" element={<DeferredRoute Page={SchedulesPage} fallback="Loading schedules..." />} />
                    <Route path="/repositories" element={<DeferredRoute Page={RepositoriesPage} fallback="Loading repositories..." />} />
                    <Route path="/knowledge/:baseId?/:titleSlug?" element={<DeferredRoute Page={KnowledgePage} fallback="Loading knowledge..." />} />
                    <Route path="/workflows/:workflowId?/:slug?" element={<DeferredRoute Page={WorkflowsPage} fallback="Loading workflows..." />} />
                    <Route path="/canvases/:name?" element={<DeferredRoute Page={CanvasesPage} fallback="Loading canvases..." />} />
                    {localDesignLibraryEnabled && (
                      <Route path="/design" element={<DeferredRoute Page={DesignLibraryPage} fallback="Loading design library..." />} />
                    )}
                  </Route>
                  <Route path="*" element={<Navigate to="/chat" replace />} />
                </Routes>
  );
}
