import { Suspense, lazy } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ConfigProvider } from './context/ConfigContext';
import { HealthProvider } from './context/HealthContext';
import { NotificationProvider } from './context/NotificationContext';
import NotificationContainer from './components/common/NotificationContainer';
import ChatPage from './pages/ChatPage';
import DashboardPage from './pages/DashboardPage';
import KanbanPage from './pages/KanbanPage';
import LogsPage from './pages/LogsPage';
import PromptsPage from './pages/PromptsPage';

// Import styles
import './styles/main.css';
import './styles/chat.css';
import './styles/dashboard.css';
import './styles/agents.css';
import './styles/logs-page.css';
import './styles/kanban.css';
import './styles/notifications.css';
import './styles/status.css';
import './styles/prompts.css';

const WorkspacePage = lazy(() => import('./pages/WorkspacePage'));

function App() {
  return (
    <ConfigProvider>
      <HealthProvider>
        <NotificationProvider>
          <BrowserRouter>
            <Routes>
              <Route path="/" element={<ChatPage />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/agents" element={<Navigate to="/dashboard" replace />} />
              <Route path="/kanban" element={<KanbanPage />} />
              <Route path="/logs" element={<LogsPage />} />
              <Route path="/prompts" element={<PromptsPage />} />
              <Route
                path="/workspace"
                element={(
                  <Suspense fallback={null}>
                    <WorkspacePage />
                  </Suspense>
                )}
              />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
            <NotificationContainer />
          </BrowserRouter>
        </NotificationProvider>
      </HealthProvider>
    </ConfigProvider>
  );
}

export default App;
