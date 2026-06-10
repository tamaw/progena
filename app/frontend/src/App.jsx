import { Suspense, lazy } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ConfigProvider } from './context/ConfigContext';
import { HealthProvider } from './context/HealthContext';
import { AlertCenterProvider } from './context/AlertCenterContext';
import { ChromeRuntimeProvider } from './context/ChromeRuntimeContext';
import { NotificationProvider } from './context/NotificationContext';
import AppRuntimeShell from './components/layout/AppRuntimeShell';
import RealtimeBridge from './store/RealtimeBridge';
import ConversationPage from './pages/ConversationPage';
import DashboardPage from './pages/DashboardPage';
import TasksPage from './pages/TasksPage';
import KanbanPage from './pages/KanbanPage';
import LogsPage from './pages/LogsPage';
import PromptsPage from './pages/PromptsPage';
import SkillsPage from './pages/SkillsPage';
import SessionsPage from './pages/SessionsPage';
import RepositoriesPage from './pages/RepositoriesPage';
import KnowledgePage from './pages/KnowledgePage';
import ToolsPage from './pages/ToolsPage';
import SchedulesPage from './pages/SchedulesPage';
import ProjectPage from './pages/ProjectPage';

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

const WorkspacePage = lazy(() => import('./pages/WorkspacePage'));
const AgentsPage = lazy(() => import('./pages/AgentsPage'));

function App() {
  return (
    <ConfigProvider>
      <RealtimeBridge />
      <HealthProvider>
        <NotificationProvider>
          <AlertCenterProvider>
            <ChromeRuntimeProvider>
              <BrowserRouter>
                <Routes>
                  <Route element={<AppRuntimeShell />}>
                    <Route path="/" element={<ConversationPage />} />
                    <Route path="/dashboard" element={<DashboardPage />} />
                    <Route
                      path="/agents/:agentId?/:tab?"
                      element={(
                        <Suspense fallback={<div className="chrome-route-loading">Loading agents...</div>}>
                          <AgentsPage />
                        </Suspense>
                      )}
                    />
                    <Route path="/tools" element={<ToolsPage />} />
                    <Route path="/tasks" element={<TasksPage />} />
                    <Route path="/projects" element={<Navigate to="/solutions/main/projects/backend-runtime" replace />} />
                    <Route path="/kanban" element={<KanbanPage />} />
                    <Route path="/solutions/:solutionId/projects/:projectId" element={<ProjectPage />} />
                    <Route path="/logs" element={<LogsPage />} />
                    <Route path="/skills" element={<SkillsPage />} />
                    <Route path="/prompts" element={<PromptsPage />} />
                    <Route path="/sessions" element={<SessionsPage />} />
                    <Route path="/schedules" element={<SchedulesPage />} />
                    <Route path="/repositories" element={<RepositoriesPage />} />
                    <Route path="/knowledge" element={<KnowledgePage />} />
                    <Route
                      path="/workspace"
                      element={(
                        <Suspense fallback={<div className="chrome-route-loading">Loading workspace...</div>}>
                          <WorkspacePage />
                        </Suspense>
                      )}
                    />
                  </Route>
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </BrowserRouter>
            </ChromeRuntimeProvider>
          </AlertCenterProvider>
        </NotificationProvider>
      </HealthProvider>
    </ConfigProvider>
  );
}

export default App;
