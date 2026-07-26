import { Component, createElement, Suspense, lazy } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ConfigProvider } from './context/ConfigContext';
import { NotificationProvider } from './context/NotificationContext';
import AppRuntimeShell from './components/layout/AppRuntimeShell';
import RealtimeBridge from './store/RealtimeBridge';

// Import styles
import './styles/main.css';
import './styles/chat-panel.css';
import './styles/dashboard.css';
import './styles/logs-page.css';
import './styles/kanban.css';
import './styles/notifications.css';
import './styles/status.css';
import './styles/prompts.css';
import './styles/chrome-frame.css';
import './styles/detail-header.css';
import './styles/forms.css';

const ConversationPage = lazy(() => import('./pages/ConversationPage'));
const InboxPage = lazy(() => import('./pages/InboxPage'));
const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const TasksPage = lazy(() => import('./pages/TasksPage'));
const KanbanPage = lazy(() => import('./pages/KanbanPage'));
const LogsPage = lazy(() => import('./pages/LogsPage'));
const PromptsPage = lazy(() => import('./pages/PromptsPage'));
const SkillsPage = lazy(() => import('./pages/SkillsPage'));
const SessionsPage = lazy(() => import('./pages/SessionsPage'));
const RepositoriesPage = lazy(() => import('./pages/RepositoriesPage'));
const KnowledgePage = lazy(() => import('./pages/KnowledgePage'));
const ToolsPage = lazy(() => import('./pages/ToolsPage'));
const SchedulesPage = lazy(() => import('./pages/SchedulesPage'));
const ProjectPage = lazy(() => import('./pages/ProjectPage'));
const ModelsPage = lazy(() => import('./pages/ModelsPage'));
const ChannelsPage = lazy(() => import('./pages/ChannelsPage'));
const WorkflowsPage = lazy(() => import('./pages/WorkflowsPage'));
const AgentsPage = lazy(() => import('./pages/AgentsPage'));

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
      return <div className="chrome-route-error">Error loading page: {this.state.error.message}</div>;
    }
    return this.props.children;
  }
}

function DeferredRoute({ Page, fallback, ...pageProps }) {
  return (
    <Suspense fallback={<div className="chrome-route-loading">{fallback}</div>}>
      <RouteErrorBoundary>
        {createElement(Page, pageProps)}
      </RouteErrorBoundary>
    </Suspense>
  );
}

function App() {
  return (
    <ConfigProvider>
      <RealtimeBridge />
      <NotificationProvider>
        <BrowserRouter>
                <Routes>
                  <Route element={<AppRuntimeShell />}>
                    <Route path="/" element={<Navigate to="/chat" replace />} />
                    <Route path="/chat/:threadId?/:titleSlug?" element={<DeferredRoute Page={ConversationPage} fallback="Loading chat..." />} />
                    <Route path="/inbox/:messageId?/:titleSlug?" element={<DeferredRoute Page={InboxPage} fallback="Loading inbox..." />} />
                    <Route path="/dashboard" element={<DeferredRoute Page={DashboardPage} fallback="Loading dashboard..." />} />
                    <Route path="/models" element={<DeferredRoute Page={ModelsPage} fallback="Loading models..." />} />
                    <Route path="/channels" element={<DeferredRoute Page={ChannelsPage} fallback="Loading channels..." />} />
                    <Route path="/agents/:agentId?/:tab?" element={<DeferredRoute Page={AgentsPage} fallback="Loading agents..." variant="agents" />} />
                    <Route path="/sequences/:agentId?" element={<DeferredRoute Page={AgentsPage} fallback="Loading sequences..." variant="sequences" />} />
                    <Route path="/tools/:serverId?/:titleSlug?" element={<DeferredRoute Page={ToolsPage} fallback="Loading tools..." />} />
                    <Route path="/tasks/:cardId?/:titleSlug?" element={<DeferredRoute Page={TasksPage} fallback="Loading tasks..." />} />
                    <Route path="/projects" element={<Navigate to="/solutions/main/projects/backend-runtime" replace />} />
                    <Route path="/kanban" element={<DeferredRoute Page={KanbanPage} fallback="Loading kanban..." />} />
                    <Route path="/solutions/:solutionId/projects/:projectId" element={<DeferredRoute Page={ProjectPage} fallback="Loading project..." />} />
                    <Route path="/logs" element={<DeferredRoute Page={LogsPage} fallback="Loading logs..." />} />
                    <Route path="/skills" element={<DeferredRoute Page={SkillsPage} fallback="Loading skills..." />} />
                    <Route path="/prompts" element={<DeferredRoute Page={PromptsPage} fallback="Loading prompts..." />} />
                    <Route path="/sessions" element={<DeferredRoute Page={SessionsPage} fallback="Loading sessions..." />} />
                    <Route path="/schedules/:scheduleId?/:titleSlug?" element={<DeferredRoute Page={SchedulesPage} fallback="Loading schedules..." />} />
                    <Route path="/repositories" element={<DeferredRoute Page={RepositoriesPage} fallback="Loading repositories..." />} />
                    <Route path="/knowledge/:baseId?/:titleSlug?" element={<DeferredRoute Page={KnowledgePage} fallback="Loading knowledge..." />} />
                    <Route path="/workflows" element={<DeferredRoute Page={WorkflowsPage} fallback="Loading workflows..." />} />
                  </Route>
                  <Route path="*" element={<Navigate to="/chat" replace />} />
                </Routes>
              </BrowserRouter>
        </NotificationProvider>
    </ConfigProvider>
  );
}

export default App;
