import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ConfigProvider } from './context/ConfigContext';
import { HealthProvider } from './context/HealthContext';
import { NotificationProvider } from './context/NotificationContext';
import NotificationContainer from './components/common/NotificationContainer';
import ChatPage from './pages/ChatPage';
import DashboardPage from './pages/DashboardPage';
import AgentsPage from './pages/AgentsPage';
import LogsPage from './pages/LogsPage';
import WorkspacePage from './pages/WorkspacePage';

// Import styles
import './styles/main.css';
import './styles/chat.css';
import './styles/dashboard.css';
import './styles/agents.css';
import './styles/logs-page.css';
import './styles/notifications.css';
import './styles/status.css';
import './styles/workspace.css';
import './styles/nodes.css';

function App() {
  return (
    <ConfigProvider>
      <HealthProvider>
        <NotificationProvider>
          <BrowserRouter>
            <Routes>
              <Route path="/" element={<ChatPage />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/agents" element={<AgentsPage />} />
              <Route path="/logs" element={<LogsPage />} />
              <Route path="/workspace" element={<WorkspacePage />} />
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
